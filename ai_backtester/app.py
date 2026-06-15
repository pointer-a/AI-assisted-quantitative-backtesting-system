from __future__ import annotations

import argparse
import tomllib
from pathlib import Path
from typing import Any

from .data import discover_year_files, load_csv, load_year_csvs, longest_contiguous_years
from .engine import BacktestEngine
from .optimizer import OptimizationCandidate, optimize_strategy, run_best
from .report import make_report_path, write_csv_exports, write_html_report, write_optimization_csv
from .strategies import create_strategy


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="从配置文件启动 AI 智能回测程序")
    parser.add_argument("--config", default="config.toml", help="配置文件路径，默认读取 config.toml")
    args = parser.parse_args(argv)
    run_from_config(args.config)


def run_from_config(config_path: str | Path = "config.toml") -> None:
    path = Path(config_path)
    config = _load_config(path)
    base_dir = path.resolve().parent

    mode = str(config.get("mode", "run")).strip().lower()
    data_config = _section(config, "data")
    strategy_config = _section(config, "strategy")
    engine_config = _section(config, "engine")
    output_config = _section(config, "output")
    optimize_config = _section(config, "optimize")

    data_path = _resolve_path(base_dir, _required(data_config, "path", "data.path"))
    configured_years = _configured_years(data_config)
    bars, used_years = _load_configured_bars(
        data_path=data_path,
        data_config=data_config,
        configured_years=configured_years,
    )
    engine = BacktestEngine(
        initial_cash=float(engine_config.get("capital", 100_000.0)),
        commission_rate=float(engine_config.get("commission", 0.0005)),
        slippage_bps=float(engine_config.get("slippage_bps", 1.0)),
    )
    strategy_name = str(strategy_config.get("name", "sma_cross"))

    print(f"配置文件：{path.resolve()}")
    print(f"行情数据：{data_path}")
    if configured_years:
        print(f"配置年份：{_format_years(configured_years)}")
        print(f"实际拼合年份：{_format_years(used_years)}")
    print(f"回测模式：{_mode_label(mode)}")
    print(f"策略名称：{_strategy_label(strategy_name)}")
    print(f"K线数量：{len(bars)}")

    if mode == "optimize":
        _run_optimization(
            bars=bars,
            strategy_name=strategy_name,
            engine=engine,
            optimize_config=optimize_config,
            output_config=output_config,
            base_dir=base_dir,
            used_years=used_years,
        )
    elif mode == "run":
        strategy = create_strategy(strategy_name, **_strategy_params(strategy_config))
        result = engine.run(bars, strategy)
        default_dir = make_report_path(strategy_name, used_years if used_years else configured_years)
        report_path = write_html_report(
            result,
            _resolve_path(base_dir, output_config.get("report", str(default_dir / "report.html"))),
            title="AI 智能回测报告",
        )
        export_dir = str(output_config.get("export_dir", "")).strip()
        if export_dir:
            write_csv_exports(result, _resolve_path(base_dir, export_dir))
        else:
            write_csv_exports(result, default_dir)
        print("\n回测结果：")
        print_summary(result.metrics)
        print(f"订单数量：{len(result.orders)}")
        print(f"报告路径：{report_path.resolve()}")
        print(f"导出目录：{export_dir if export_dir else default_dir.resolve()}")
    else:
        raise ValueError("mode 只能配置为 run 或 optimize")


def print_summary(metrics: dict[str, float]) -> None:
    for key in [
        "final_equity",
        "total_return",
        "buy_hold_return",
        "cagr",
        "max_drawdown",
        "sharpe",
        "trade_count",
        "win_rate",
        "profit_factor",
    ]:
        value = metrics.get(key, 0.0)
        print(f"{METRIC_LABELS.get(key, key)}：{_format_terminal_metric(key, value)}")


def print_candidates(candidates: list[OptimizationCandidate], top_n: int = 5) -> None:
    print("\n优化候选参数：")
    for index, candidate in enumerate(candidates[:top_n], start=1):
        print(
            f"{index}. 综合评分={candidate.combined_score:.4f} "
            f"训练评分={candidate.train_score:.4f} "
            f"测试评分={candidate.test_score:.4f} "
            f"参数={candidate.params}"
        )


def _run_optimization(
    *,
    bars,
    strategy_name: str,
    engine: BacktestEngine,
    optimize_config: dict[str, Any],
    output_config: dict[str, Any],
    base_dir: Path,
    used_years: list[int],
) -> None:
    candidates = optimize_strategy(
        bars=bars,
        strategy_name=strategy_name,
        engine=engine,
        trials=int(optimize_config.get("trials", 100)),
        train_ratio=float(optimize_config.get("train_ratio", 0.7)),
        seed=int(optimize_config.get("seed", 7)),
    )
    if not candidates:
        raise SystemExit("没有找到有效的优化候选参数")

    print_candidates(candidates, top_n=int(output_config.get("top_n", 5)))
    result = run_best(bars, strategy_name, engine, candidates)
    default_dir = make_report_path(strategy_name, used_years)
    report_path = write_html_report(
        result,
        _resolve_path(base_dir, output_config.get("report", str(default_dir / "report.html"))),
        title="AI 智能优化回测报告",
    )
    optimization_path = write_optimization_csv(
        candidates,
        _resolve_path(base_dir, output_config.get("optimization_csv", str(default_dir / "candidates.csv"))),
    )
    export_dir = str(output_config.get("export_dir", "")).strip()
    if export_dir:
        write_csv_exports(result, _resolve_path(base_dir, export_dir))
    else:
        write_csv_exports(result, default_dir)

    print("\n最佳参数全周期回测结果：")
    print_summary(result.metrics)
    print(f"订单数量：{len(result.orders)}")
    print(f"报告路径：{report_path.resolve()}")
    print(f"优化结果CSV：{optimization_path.resolve()}")
    print(f"导出目录：{export_dir if export_dir else default_dir.resolve()}")


def _load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"找不到配置文件：{path}")
    with path.open("rb") as handle:
        return tomllib.load(handle)


def _load_configured_bars(
    *,
    data_path: Path,
    data_config: dict[str, Any],
    configured_years: list[int],
) -> tuple[list, list[int]]:
    resample = str(data_config.get("resample", "none"))
    if not configured_years:
        return load_csv(data_path, resample=resample), []

    year_files = discover_year_files(data_path)
    available_years = sorted(year_files)
    missing_years = [year for year in configured_years if year not in year_files]
    if missing_years:
        usable_years = [year for year in configured_years if year in year_files]
        max_years = longest_contiguous_years(usable_years)
        print("\n年份数据不满足配置要求：")
        print(f"配置要求年份：{_format_years(configured_years)}")
        print(f"现有数据年份：{_format_years(available_years) if available_years else '无'}")
        print(f"缺失年份：{_format_years(missing_years)}")
        print(f"现有能拼合的最大连续年份：{_format_years(max_years) if max_years else '无'}")
        if not max_years:
            raise SystemExit("没有可用于继续回测的年份数据")
        if not _confirm_continue("是否继续使用现有最大连续年份进行回测？"):
            raise SystemExit("用户取消执行")
        configured_years = max_years

    return load_year_csvs(data_path, configured_years, resample=resample), configured_years


def _configured_years(data_config: dict[str, Any]) -> list[int]:
    if "years" in data_config:
        years_value = data_config["years"]
        if not isinstance(years_value, list):
            raise ValueError("data.years 必须配置为年份数组，例如 [2021, 2022, 2023]")
        return sorted({int(year) for year in years_value})

    if "year" in data_config:
        return [int(data_config["year"])]

    if "start_year" in data_config or "end_year" in data_config:
        start_year = int(_required(data_config, "start_year", "data.start_year"))
        end_year = int(_required(data_config, "end_year", "data.end_year"))
        if start_year > end_year:
            raise ValueError("data.start_year 不能大于 data.end_year")
        return list(range(start_year, end_year + 1))

    return []


def _confirm_continue(question: str) -> bool:
    answer = input(f"{question} 输入 y 继续，其他任意键取消：").strip().lower()
    return answer in {"y", "yes", "是", "继续"}


def _format_years(years: list[int]) -> str:
    return ", ".join(str(year) for year in years)


def _section(config: dict[str, Any], key: str) -> dict[str, Any]:
    value = config.get(key, {})
    if not isinstance(value, dict):
        raise ValueError(f"配置项 {key} 必须是一个表")
    return value


def _required(config: dict[str, Any], key: str, label: str) -> Any:
    value = config.get(key)
    if value in {None, ""}:
        raise ValueError(f"缺少必要配置：{label}")
    return value


def _resolve_path(base_dir: Path, value: Any) -> Path:
    path = Path(str(value))
    if path.is_absolute():
        return path
    return base_dir / path


def _strategy_params(strategy_config: dict[str, Any]) -> dict[str, Any]:
    ignored = {"name"}
    return {key: value for key, value in strategy_config.items() if key not in ignored}


def _format_terminal_metric(key: str, value: float) -> str:
    if key in {
        "total_return",
        "buy_hold_return",
        "cagr",
        "max_drawdown",
        "win_rate",
        "best_trade",
        "worst_trade",
        "avg_trade",
        "expectancy",
        "exposure_time",
    }:
        return f"{value:.2%}"
    if key in {"trade_count", "order_count", "duration_bars"}:
        return f"{value:.0f}"
    if value == float("inf"):
        return "无限大"
    return f"{value:,.4f}"


def _mode_label(mode: str) -> str:
    return {"run": "普通回测", "optimize": "智能参数优化"}.get(mode, mode)


def _strategy_label(name: str) -> str:
    labels = {
        "buy_hold": "买入持有",
        "sma_cross": "均线交叉",
        "rsi_reversion": "RSI 均值回归",
        "hybrid_trend_rsi": "趋势 + RSI 混合策略",
    }
    return labels.get(name, name)


METRIC_LABELS = {
    "start": "开始时间",
    "end": "结束时间",
    "duration_bars": "回测K线数",
    "initial_cash": "初始资金",
    "final_equity": "最终权益",
    "equity_peak": "权益峰值",
    "exposure_time": "持仓时间占比",
    "total_return": "总收益率",
    "buy_hold_return": "买入持有收益率",
    "cagr": "年化复合收益率",
    "max_drawdown": "最大回撤",
    "volatility": "年化波动率",
    "sharpe": "夏普比率",
    "calmar": "卡玛比率",
    "order_count": "订单数量",
    "commission_total": "手续费合计",
    "trade_count": "完整交易次数",
    "win_rate": "胜率",
    "best_trade": "最佳单笔收益",
    "worst_trade": "最差单笔收益",
    "avg_trade": "平均单笔收益",
    "expectancy": "单笔期望收益",
    "profit_factor": "利润因子",
    "sqn": "系统质量指数",
    "kelly": "Kelly 仓位参考",
}

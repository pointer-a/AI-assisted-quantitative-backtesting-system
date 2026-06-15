from __future__ import annotations

import argparse
from pathlib import Path

from .app import print_candidates, print_summary
from .data import load_csv
from .engine import BacktestEngine
from .optimizer import optimize_strategy, run_best
from .report import write_csv_exports, write_html_report, write_optimization_csv
from .strategies import create_strategy


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="ai-backtester", description="AI 智能回测程序命令行入口")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="执行一次普通回测")
    _add_common_args(run_parser)
    _add_strategy_args(run_parser)
    run_parser.add_argument("--report", default="reports/backtest_report.html")
    run_parser.add_argument("--export-dir", default="", help="可选：导出净值、订单、交易、指标 CSV 的目录")

    optimize_parser = subparsers.add_parser("optimize", help="搜索更稳健的策略参数")
    _add_common_args(optimize_parser)
    optimize_parser.add_argument("--trials", type=int, default=100)
    optimize_parser.add_argument("--seed", type=int, default=7)
    optimize_parser.add_argument("--train-ratio", type=float, default=0.7)
    optimize_parser.add_argument("--report", default="reports/optimized_report.html")
    optimize_parser.add_argument("--export-dir", default="", help="可选：导出最优结果 CSV 的目录")
    optimize_parser.add_argument("--optimization-csv", default="reports/optimization_candidates.csv")

    args = parser.parse_args(argv)
    if args.command == "run":
        _run(args)
    elif args.command == "optimize":
        _optimize(args)


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--data", required=True, help="CSV 行情文件，需包含时间、开高低收、成交量")
    parser.add_argument("--strategy", default="sma_cross", help="buy_hold, sma_cross, rsi_reversion, hybrid_trend_rsi")
    parser.add_argument("--capital", type=float, default=100_000.0)
    parser.add_argument("--commission", type=float, default=0.0005)
    parser.add_argument("--slippage-bps", type=float, default=1.0)
    parser.add_argument("--resample", choices=["none", "daily", "hourly"], default="none")


def _add_strategy_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--fast", type=int, default=10)
    parser.add_argument("--slow", type=int, default=30)
    parser.add_argument("--period", type=int, default=14)
    parser.add_argument("--buy-below", type=float, default=35.0)
    parser.add_argument("--sell-above", type=float, default=65.0)
    parser.add_argument("--rsi-period", type=int, default=14)
    parser.add_argument("--max-rsi", type=float, default=72.0)


def _engine_from_args(args) -> BacktestEngine:
    return BacktestEngine(
        initial_cash=args.capital,
        commission_rate=args.commission,
        slippage_bps=args.slippage_bps,
    )


def _run(args) -> None:
    bars = load_csv(args.data, resample=args.resample)
    engine = _engine_from_args(args)
    strategy = create_strategy(
        args.strategy,
        fast=args.fast,
        slow=args.slow,
        period=args.period,
        buy_below=args.buy_below,
        sell_above=args.sell_above,
        rsi_period=args.rsi_period,
        max_rsi=args.max_rsi,
    )
    result = engine.run(bars, strategy)
    report_path = write_html_report(result, args.report, title="AI 智能回测报告")
    export_paths = write_csv_exports(result, args.export_dir) if args.export_dir else {}
    print_summary(result.metrics)
    print(f"订单数量：{len(result.orders)}")
    print(f"报告路径：{Path(report_path).resolve()}")
    if export_paths:
        print(f"导出目录：{Path(args.export_dir).resolve()}")


def _optimize(args) -> None:
    bars = load_csv(args.data, resample=args.resample)
    engine = _engine_from_args(args)
    candidates = optimize_strategy(
        bars=bars,
        strategy_name=args.strategy,
        engine=engine,
        trials=args.trials,
        train_ratio=args.train_ratio,
        seed=args.seed,
    )
    if not candidates:
        raise SystemExit("没有找到有效的优化候选参数")

    print_candidates(candidates, top_n=5)

    result = run_best(bars, args.strategy, engine, candidates)
    report_path = write_html_report(result, args.report, title="AI 智能优化回测报告")
    optimization_path = write_optimization_csv(candidates, args.optimization_csv)
    export_paths = write_csv_exports(result, args.export_dir) if args.export_dir else {}
    print("\n最佳参数全周期回测结果：")
    print_summary(result.metrics)
    print(f"订单数量：{len(result.orders)}")
    print(f"报告路径：{Path(report_path).resolve()}")
    print(f"优化结果CSV：{Path(optimization_path).resolve()}")
    if export_paths:
        print(f"导出目录：{Path(args.export_dir).resolve()}")

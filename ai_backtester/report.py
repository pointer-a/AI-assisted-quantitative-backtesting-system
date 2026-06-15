from __future__ import annotations

import csv
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any

from .models import BacktestResult


def write_html_report(result: BacktestResult, path: str | Path, title: str = "回测报告") -> Path:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    metrics_rows = "\n".join(
        f"<tr><th>{escape(METRIC_LABELS.get(key, key))}</th><td>{_format_metric(key, value)}</td></tr>"
        for key, value in result.metrics.items()
    )
    trade_rows = "\n".join(
        "<tr>"
        f"<td>{trade.entry_date}</td><td>{trade.exit_date}</td>"
        f"<td>{trade.entry_price:.2f}</td><td>{trade.exit_price:.2f}</td>"
        f"<td>{trade.pnl:.2f}</td><td>{trade.return_pct:.2%}</td>"
        "</tr>"
        for trade in result.round_trips[-50:]
    )
    chart = _equity_svg(result)
    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)}</title>
  <style>
    body {{ margin: 0; font-family: Arial, sans-serif; background: #f6f7f9; color: #172033; }}
    main {{ max-width: 1100px; margin: 0 auto; padding: 28px; }}
    h1 {{ font-size: 28px; margin: 0 0 6px; }}
    .muted {{ color: #667085; margin-bottom: 24px; }}
    section {{ background: white; border: 1px solid #d9dee8; border-radius: 8px; padding: 18px; margin: 16px 0; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
    th, td {{ border-bottom: 1px solid #edf0f5; padding: 10px; text-align: right; }}
    th:first-child, td:first-child {{ text-align: left; }}
    svg {{ width: 100%; height: auto; display: block; }}
  </style>
</head>
<body>
  <main>
    <h1>{escape(title)}</h1>
    <div class="muted">策略：{escape(_strategy_label(result.strategy_name))} | 初始资金：{result.initial_cash:,.2f} | 最终权益：{result.final_equity:,.2f}</div>
    <section>{chart}</section>
    <section><h2>绩效指标</h2><table>{metrics_rows}</table></section>
    <section><h2>最近完整交易</h2><table><tr><th>入场时间</th><th>出场时间</th><th>入场价格</th><th>出场价格</th><th>盈亏</th><th>收益率</th></tr>{trade_rows}</table></section>
  </main>
</body>
</html>
"""
    output_path.write_text(html, encoding="utf-8")
    return output_path


def write_csv_exports(result: BacktestResult, directory: str | Path) -> dict[str, Path]:
    output_dir = Path(directory)
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "equity": output_dir / "净值曲线.csv",
        "orders": output_dir / "订单记录.csv",
        "trades": output_dir / "完整交易.csv",
        "metrics": output_dir / "绩效指标.csv",
    }

    with paths["equity"].open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["时间", "权益", "现金", "持仓数量", "价格", "回撤比例"])
        peak = result.equity_curve[0].equity if result.equity_curve else 0.0
        for point in result.equity_curve:
            peak = max(peak, point.equity)
            drawdown = (point.equity / peak) - 1.0 if peak > 0 else 0.0
            writer.writerow([point.date, point.equity, point.cash, point.position, point.price, drawdown])

    with paths["orders"].open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["时间", "方向", "价格", "数量", "成交金额", "手续费"])
        for order in result.orders:
            writer.writerow([order.date, _side_label(order.side), order.price, order.shares, order.value, order.commission])

    with paths["trades"].open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["入场时间", "出场时间", "入场价格", "出场价格", "数量", "盈亏", "收益率"])
        for trade in result.round_trips:
            writer.writerow(
                [
                    trade.entry_date,
                    trade.exit_date,
                    trade.entry_price,
                    trade.exit_price,
                    trade.shares,
                    trade.pnl,
                    trade.return_pct,
                ]
            )

    with paths["metrics"].open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["指标", "数值"])
        for key, value in result.metrics.items():
            writer.writerow([METRIC_LABELS.get(key, key), _format_metric(key, value)])

    return paths


def write_optimization_csv(candidates: list[Any], path: str | Path) -> Path:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    metric_keys = sorted({key for item in candidates for key in item.test_metrics})
    param_keys = sorted({key for item in candidates for key in item.params})

    with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["排名", "综合评分", "训练评分", "测试评分"]
            + [f"参数_{PARAM_LABELS.get(key, key)}" for key in param_keys]
            + [f"测试_{METRIC_LABELS.get(key, key)}" for key in metric_keys]
        )
        for rank, item in enumerate(candidates, start=1):
            writer.writerow(
                [rank, item.combined_score, item.train_score, item.test_score]
                + [item.params.get(key, "") for key in param_keys]
                + [_format_metric(key, item.test_metrics.get(key, 0.0)) for key in metric_keys]
            )

    return output_path


def _equity_svg(result: BacktestResult) -> str:
    points = result.equity_curve
    width = 1000
    height = 320
    padding = 36
    values = [point.equity for point in points]
    low = min(values)
    high = max(values)
    span = high - low or 1.0
    x_step = (width - padding * 2) / max(1, len(points) - 1)

    coords = []
    for index, point in enumerate(points):
        x = padding + index * x_step
        y = height - padding - ((point.equity - low) / span) * (height - padding * 2)
        coords.append(f"{x:.1f},{y:.1f}")

    return f"""
<svg viewBox="0 0 {width} {height}" role="img" aria-label="净值曲线">
  <rect x="0" y="0" width="{width}" height="{height}" fill="#ffffff"/>
  <line x1="{padding}" y1="{height - padding}" x2="{width - padding}" y2="{height - padding}" stroke="#c7cedb"/>
  <line x1="{padding}" y1="{padding}" x2="{padding}" y2="{height - padding}" stroke="#c7cedb"/>
  <polyline fill="none" stroke="#1f7a8c" stroke-width="3" points="{' '.join(coords)}"/>
  <text x="{padding}" y="22" fill="#667085" font-size="13">最高 {high:,.2f}</text>
  <text x="{padding}" y="{height - 10}" fill="#667085" font-size="13">最低 {low:,.2f}</text>
</svg>
"""


def _format_metric(key: str, value: float) -> str:
    if key in {"start", "end"}:
        return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M:%S")
    if key in {
        "total_return",
        "buy_hold_return",
        "cagr",
        "max_drawdown",
        "volatility",
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


def _strategy_label(name: str) -> str:
    labels = {
        "buy_hold": "买入持有",
        "sma_cross": "均线交叉",
        "rsi_reversion": "RSI 均值回归",
        "hybrid_trend_rsi": "趋势 + RSI 混合策略",
    }
    return labels.get(name, name)


def _side_label(side: str) -> str:
    return {"BUY": "买入", "SELL": "卖出"}.get(side, side)


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


PARAM_LABELS = {
    "fast": "快线周期",
    "slow": "慢线周期",
    "period": "周期",
    "buy_below": "买入阈值",
    "sell_above": "卖出阈值",
    "rsi_period": "RSI周期",
    "max_rsi": "RSI上限",
}

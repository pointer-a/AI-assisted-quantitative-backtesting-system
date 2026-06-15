from __future__ import annotations

import math
from datetime import date, datetime

from .models import Bar, EquityPoint, Order, RoundTrip


TRADING_DAYS_PER_YEAR = 252


def calculate_metrics(
    equity_curve: list[EquityPoint],
    round_trips: list[RoundTrip],
    initial_cash: float,
    bars: list[Bar],
    orders: list[Order],
) -> dict[str, float]:
    if not equity_curve:
        return {}

    equities = [point.equity for point in equity_curve]
    returns = [
        (current / previous) - 1.0
        for previous, current in zip(equities, equities[1:])
        if previous > 0
    ]

    final_equity = equities[-1]
    total_return = (final_equity / initial_cash) - 1.0
    years = max(len(equity_curve) / TRADING_DAYS_PER_YEAR, 1 / TRADING_DAYS_PER_YEAR)
    cagr = (final_equity / initial_cash) ** (1 / years) - 1.0 if final_equity > 0 else -1.0
    max_drawdown = _max_drawdown(equities)
    volatility = _stddev(returns) * math.sqrt(TRADING_DAYS_PER_YEAR) if returns else 0.0
    sharpe = _sharpe(returns)
    wins = [trade for trade in round_trips if trade.pnl > 0]
    losses = [trade for trade in round_trips if trade.pnl <= 0]
    gross_profit = sum(trade.pnl for trade in wins)
    gross_loss = abs(sum(trade.pnl for trade in losses))
    trade_returns = [trade.return_pct for trade in round_trips]
    best_trade = max(trade_returns) if trade_returns else 0.0
    worst_trade = min(trade_returns) if trade_returns else 0.0
    avg_trade = _geometric_mean(trade_returns) if trade_returns else 0.0
    exposure_time = _exposure_time(equity_curve)
    buy_hold_return = (bars[-1].close / bars[0].close) - 1.0 if bars and bars[0].close > 0 else 0.0
    commission_total = sum(order.commission for order in orders)
    pnl_values = [trade.pnl for trade in round_trips]
    win_rate = len(wins) / len(round_trips) if round_trips else 0.0
    avg_win = sum(trade.pnl for trade in wins) / len(wins) if wins else 0.0
    avg_loss = abs(sum(trade.pnl for trade in losses) / len(losses)) if losses else 0.0

    return {
        "start": _date_to_number(equity_curve[0].date),
        "end": _date_to_number(equity_curve[-1].date),
        "duration_bars": float(len(equity_curve) - 1),
        "initial_cash": initial_cash,
        "final_equity": final_equity,
        "equity_peak": max(equities),
        "exposure_time": exposure_time,
        "total_return": total_return,
        "buy_hold_return": buy_hold_return,
        "cagr": cagr,
        "max_drawdown": max_drawdown,
        "volatility": volatility,
        "sharpe": sharpe,
        "calmar": cagr / abs(max_drawdown) if max_drawdown < 0 else 0.0,
        "order_count": float(len(orders)),
        "commission_total": commission_total,
        "trade_count": float(len(round_trips)),
        "win_rate": win_rate,
        "best_trade": best_trade,
        "worst_trade": worst_trade,
        "avg_trade": avg_trade,
        "expectancy": sum(trade_returns) / len(trade_returns) if trade_returns else 0.0,
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else float("inf") if gross_profit > 0 else 0.0,
        "sqn": math.sqrt(len(pnl_values)) * (sum(pnl_values) / len(pnl_values)) / _stddev(pnl_values)
        if len(pnl_values) > 1 and _stddev(pnl_values) > 0 else 0.0,
        "kelly": win_rate - ((1 - win_rate) / (avg_win / avg_loss)) if avg_win > 0 and avg_loss > 0 else 0.0,
    }


def _max_drawdown(equities: list[float]) -> float:
    peak = equities[0]
    worst = 0.0
    for equity in equities:
        peak = max(peak, equity)
        drawdown = (equity / peak) - 1.0 if peak > 0 else 0.0
        worst = min(worst, drawdown)
    return worst


def _sharpe(returns: list[float]) -> float:
    if len(returns) < 2:
        return 0.0
    deviation = _stddev(returns)
    if deviation == 0:
        return 0.0
    return (sum(returns) / len(returns)) / deviation * math.sqrt(TRADING_DAYS_PER_YEAR)


def _stddev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    average = sum(values) / len(values)
    variance = sum((value - average) ** 2 for value in values) / (len(values) - 1)
    return math.sqrt(variance)


def _exposure_time(equity_curve: list[EquityPoint]) -> float:
    if not equity_curve:
        return 0.0
    invested = sum(1 for point in equity_curve if abs(point.position) > 1e-9)
    return invested / len(equity_curve)


def _geometric_mean(returns: list[float]) -> float:
    product = 1.0
    for value in returns:
        if value <= -1.0:
            return -1.0
        product *= 1.0 + value
    return product ** (1.0 / len(returns)) - 1.0


def _date_to_number(value: date | datetime) -> float:
    if isinstance(value, datetime):
        return value.timestamp()
    return datetime(value.year, value.month, value.day).timestamp()

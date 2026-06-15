from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class Bar:
    date: date | datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass(frozen=True)
class EquityPoint:
    date: date | datetime
    equity: float
    cash: float
    position: float
    price: float


@dataclass(frozen=True)
class Order:
    date: date | datetime
    side: str
    price: float
    shares: float
    value: float
    commission: float


@dataclass(frozen=True)
class RoundTrip:
    entry_date: date | datetime
    exit_date: date | datetime
    entry_price: float
    exit_price: float
    shares: float
    pnl: float
    return_pct: float


@dataclass(frozen=True)
class CapitalEvent:
    date: date | datetime
    event_type: str
    amount: float
    equity_before: float
    equity_after: float


@dataclass(frozen=True)
class BacktestResult:
    strategy_name: str
    initial_cash: float
    final_equity: float
    equity_curve: list[EquityPoint]
    orders: list[Order]
    round_trips: list[RoundTrip]
    capital_events: list[CapitalEvent]
    metrics: dict[str, float]

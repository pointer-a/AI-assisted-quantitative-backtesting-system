from __future__ import annotations

from typing import Callable

from .metrics import calculate_metrics
from .models import BacktestResult, Bar, CapitalEvent, EquityPoint, Order, RoundTrip
from .strategies import Strategy


class BacktestEngine:
    def __init__(
        self,
        initial_cash: float = 100_000.0,
        commission_rate: float = 0.0005,
        slippage_bps: float = 1.0,
        auto_replenish: bool = True,
        replenish_amount: float | None = None,
        zero_equity_threshold: float | None = None,
    ) -> None:
        if initial_cash <= 0:
            raise ValueError("initial_cash must be positive")
        self.initial_cash = initial_cash
        self.commission_rate = commission_rate
        self.slippage_bps = slippage_bps
        self.auto_replenish = auto_replenish
        self.replenish_amount = replenish_amount if replenish_amount is not None else initial_cash
        self.zero_equity_threshold = zero_equity_threshold if zero_equity_threshold is not None else max(1.0, initial_cash * 0.00001)

    def run(
        self,
        bars: list[Bar],
        strategy: Strategy,
        progress_callback: Callable[[int, int, Bar], None] | None = None,
    ) -> BacktestResult:
        if len(bars) < 3:
            raise ValueError("Need at least 3 bars")

        cash = self.initial_cash
        position = 0.0
        orders: list[Order] = []
        round_trips: list[RoundTrip] = []
        capital_events: list[CapitalEvent] = []
        equity_curve: list[EquityPoint] = [
            EquityPoint(bars[0].date, self.initial_cash, cash, position, bars[0].close)
        ]

        entry_date = None
        entry_price = 0.0
        entry_shares = 0.0
        entry_cost = 0.0

        history = [bars[0]]
        last_progress_percent = -1
        for index in range(1, len(bars)):
            execution_bar = bars[index]
            current_value = position * execution_bar.open
            portfolio_value = cash + current_value
            current_exposure = current_value / portfolio_value if portfolio_value > 0 else 0.0
            target_exposure = _clamp(strategy.target_exposure(history, current_exposure), 0.0, 1.0)
            target_value = portfolio_value * target_exposure
            delta_value = target_value - current_value

            if abs(delta_value) > max(1.0, portfolio_value * 0.001):
                if delta_value > 0:
                    price = execution_bar.open * (1.0 + self.slippage_bps / 10_000.0)
                    buy_value = min(delta_value, cash / (1.0 + self.commission_rate))
                    shares = buy_value / price
                    commission = buy_value * self.commission_rate
                    if shares > 0:
                        cash -= buy_value + commission
                        position += shares
                        orders.append(Order(execution_bar.date, "BUY", price, shares, buy_value, commission))
                        if entry_date is None:
                            entry_date = execution_bar.date
                            entry_cost = buy_value + commission
                            entry_shares = shares
                            entry_price = entry_cost / entry_shares
                        else:
                            entry_cost += buy_value + commission
                            entry_shares += shares
                            entry_price = entry_cost / entry_shares
                else:
                    price = execution_bar.open * (1.0 - self.slippage_bps / 10_000.0)
                    shares = min(position, abs(delta_value) / price)
                    sell_value = shares * price
                    commission = sell_value * self.commission_rate
                    if shares > 0:
                        cash += sell_value - commission
                        position -= shares
                        orders.append(Order(execution_bar.date, "SELL", price, shares, sell_value, commission))

                        if entry_date is not None and position <= 1e-9:
                            exit_value = price * entry_shares - commission
                            pnl = exit_value - entry_cost
                            round_trips.append(
                                RoundTrip(
                                    entry_date=entry_date,
                                    exit_date=execution_bar.date,
                                    entry_price=entry_price,
                                    exit_price=price,
                                    shares=entry_shares,
                                    pnl=pnl,
                                    return_pct=(pnl / entry_cost) if entry_cost > 0 else 0.0,
                                )
                            )
                            entry_date = None
                            entry_price = 0.0
                            entry_shares = 0.0
                            entry_cost = 0.0

            equity = cash + position * execution_bar.close
            equity_curve.append(EquityPoint(execution_bar.date, equity, cash, position, execution_bar.close))
            if self.auto_replenish and position <= 1e-9 and equity <= self.zero_equity_threshold:
                capital_events.append(
                    CapitalEvent(
                        date=execution_bar.date,
                        event_type="zero",
                        amount=0.0,
                        equity_before=equity,
                        equity_after=equity,
                    )
                )
                cash += self.replenish_amount
                equity = cash
                capital_events.append(
                    CapitalEvent(
                        date=execution_bar.date,
                        event_type="replenish",
                        amount=self.replenish_amount,
                        equity_before=equity_curve[-1].equity,
                        equity_after=equity,
                    )
                )
                equity_curve[-1] = EquityPoint(execution_bar.date, equity, cash, position, execution_bar.close)
            history.append(execution_bar)
            if progress_callback is not None:
                progress_percent = int(index / (len(bars) - 1) * 100)
                if progress_percent != last_progress_percent:
                    last_progress_percent = progress_percent
                    progress_callback(index, len(bars), execution_bar)

        if position > 0:
            final_bar = bars[-1]
            price = final_bar.close * (1.0 - self.slippage_bps / 10_000.0)
            sell_value = position * price
            commission = sell_value * self.commission_rate
            cash += sell_value - commission
            orders.append(Order(final_bar.date, "SELL", price, position, sell_value, commission))
            if entry_date is not None:
                exit_value = price * entry_shares - commission
                pnl = exit_value - entry_cost
                round_trips.append(
                    RoundTrip(
                        entry_date=entry_date,
                        exit_date=final_bar.date,
                        entry_price=entry_price,
                        exit_price=price,
                        shares=entry_shares,
                        pnl=pnl,
                        return_pct=(pnl / entry_cost) if entry_cost > 0 else 0.0,
                    )
                )
            equity_curve[-1] = EquityPoint(final_bar.date, cash, cash, 0.0, final_bar.close)
            position = 0.0

        final_equity = equity_curve[-1].equity
        metrics = calculate_metrics(equity_curve, round_trips, self.initial_cash, bars, orders)
        metrics["capital_event_count"] = float(len(capital_events))
        metrics["capital_replenishment_count"] = float(sum(1 for event in capital_events if event.event_type == "replenish"))
        metrics["capital_replenishment_total"] = sum(event.amount for event in capital_events if event.event_type == "replenish")
        return BacktestResult(
            strategy_name=strategy.name,
            initial_cash=self.initial_cash,
            final_equity=final_equity,
            equity_curve=equity_curve,
            orders=orders,
            round_trips=round_trips,
            capital_events=capital_events,
            metrics=metrics,
        )


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))

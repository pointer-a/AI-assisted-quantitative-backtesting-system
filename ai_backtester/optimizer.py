from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any

from .data import split_bars
from .engine import BacktestEngine
from .models import Bar, BacktestResult
from .strategies import create_strategy


@dataclass(frozen=True)
class OptimizationCandidate:
    params: dict[str, Any]
    train_score: float
    test_score: float
    combined_score: float
    train_metrics: dict[str, float]
    test_metrics: dict[str, float]


def optimize_strategy(
    bars: list[Bar],
    strategy_name: str,
    engine: BacktestEngine,
    trials: int = 100,
    train_ratio: float = 0.7,
    seed: int = 7,
) -> list[OptimizationCandidate]:
    if trials <= 0:
        raise ValueError("trials must be positive")

    rng = random.Random(seed)
    train_bars, test_bars = split_bars(bars, train_ratio)
    candidates: list[OptimizationCandidate] = []

    for _ in range(trials):
        params = _sample_params(strategy_name, rng)
        try:
            train_result = engine.run(train_bars, create_strategy(strategy_name, **params))
            test_result = engine.run(test_bars, create_strategy(strategy_name, **params))
        except ValueError:
            continue

        train_score = score_metrics(train_result.metrics)
        test_score = score_metrics(test_result.metrics)
        stability_penalty = abs(train_score - test_score) * 0.25
        combined_score = (0.4 * train_score) + (0.6 * test_score) - stability_penalty
        candidates.append(
            OptimizationCandidate(
                params=params,
                train_score=train_score,
                test_score=test_score,
                combined_score=combined_score,
                train_metrics=train_result.metrics,
                test_metrics=test_result.metrics,
            )
        )

    candidates.sort(key=lambda item: item.combined_score, reverse=True)
    return candidates


def run_best(
    bars: list[Bar],
    strategy_name: str,
    engine: BacktestEngine,
    candidates: list[OptimizationCandidate],
) -> BacktestResult:
    if not candidates:
        raise ValueError("No optimization candidates")
    strategy = create_strategy(strategy_name, **candidates[0].params)
    return engine.run(bars, strategy)


def score_metrics(metrics: dict[str, float]) -> float:
    cagr = _finite(metrics.get("cagr", 0.0))
    sharpe = _finite(metrics.get("sharpe", 0.0))
    drawdown = abs(_finite(metrics.get("max_drawdown", 0.0)))
    trades = _finite(metrics.get("trade_count", 0.0))
    activity_bonus = min(trades, 20.0) / 100.0
    return (cagr * 2.0) + (sharpe * 0.35) - (drawdown * 1.5) + activity_bonus


def _sample_params(strategy_name: str, rng: random.Random) -> dict[str, Any]:
    name = strategy_name.strip().lower()
    if name in {"sma_cross", "sma", "ma"}:
        fast = rng.randint(3, 40)
        slow = rng.randint(fast + 5, 140)
        return {"fast": fast, "slow": slow}
    if name in {"rsi_reversion", "rsi"}:
        buy_below = rng.randint(20, 45)
        sell_above = rng.randint(max(55, buy_below + 10), 85)
        return {
            "period": rng.randint(5, 30),
            "buy_below": buy_below,
            "sell_above": sell_above,
        }
    if name in {"hybrid_trend_rsi", "hybrid"}:
        fast = rng.randint(5, 35)
        slow = rng.randint(fast + 10, 160)
        return {
            "fast": fast,
            "slow": slow,
            "rsi_period": rng.randint(7, 30),
            "max_rsi": rng.randint(55, 82),
        }
    return {}


def _finite(value: float) -> float:
    if value != value or value in {float("inf"), float("-inf")}:
        return 0.0
    return value

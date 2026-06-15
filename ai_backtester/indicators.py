from __future__ import annotations


def sma(values: list[float], period: int) -> float | None:
    if period <= 0:
        raise ValueError("period must be positive")
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def rsi(values: list[float], period: int = 14) -> float | None:
    if period <= 0:
        raise ValueError("period must be positive")
    if len(values) <= period:
        return None

    gains = 0.0
    losses = 0.0
    window = values[-(period + 1) :]
    for previous, current in zip(window, window[1:]):
        change = current - previous
        if change >= 0:
            gains += change
        else:
            losses -= change

    average_gain = gains / period
    average_loss = losses / period
    if average_loss == 0:
        return 100.0
    relative_strength = average_gain / average_loss
    return 100.0 - (100.0 / (1.0 + relative_strength))

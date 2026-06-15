from __future__ import annotations

from numbers import Number
from typing import Sequence


def crossover(series1: Sequence[float] | Number, series2: Sequence[float] | Number) -> bool:
    """Return True when series1 crosses above series2 on the latest bar."""
    left = _last_two(series1)
    right = _last_two(series2)
    if left is None or right is None:
        return False
    return left[0] <= right[0] and left[1] > right[1]


def cross(series1: Sequence[float] | Number, series2: Sequence[float] | Number) -> bool:
    """Return True when two series cross in either direction on the latest bar."""
    return crossover(series1, series2) or crossover(series2, series1)


def barssince(condition: Sequence[bool], default: int | float = float("inf")) -> int | float:
    """Return the number of bars since condition was last true."""
    for offset, value in enumerate(reversed(condition)):
        if value:
            return offset
    return default


def _last_two(value: Sequence[float] | Number) -> tuple[float, float] | None:
    if isinstance(value, Number):
        number = float(value)
        return number, number
    if len(value) < 2:
        return None
    return float(value[-2]), float(value[-1])

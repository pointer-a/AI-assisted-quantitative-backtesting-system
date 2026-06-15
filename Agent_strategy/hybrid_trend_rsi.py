"""
趋势 + RSI 过滤混合策略 (Hybrid Trend + RSI Filter)
====================================================

策略逻辑
--------
将两条 SMA 均线的趋势判断与 RSI 情绪过滤相结合，仅在"趋势向上 且 市场不过热"时入场。

- **入场条件（同时满足）**：
    1. 快线 SMA > 慢线 SMA（趋势向上）
    2. RSI < max_rsi（市场未过热）

- **退出条件（满足任一）**：
    1. 快线 SMA ≤ 慢线 SMA（趋势破坏）
    2. RSI ≥ max_rsi（市场过热）

- **空仓等待**：
    任一条件不满足时平仓并保持空仓。

设计思路
--------
纯均线交叉在震荡市中假信号多；纯 RSI 回归在强趋势中容易逆势。
两相结合：
- SMA 提供方向过滤：只在上升趋势中考虑入场
- RSI 提供情绪过滤：趋势向好但已过度投机时管住手

这是一种"既要趋势、又怕追高"的保守型趋势策略。

目标仓位
--------
- 快线 > 慢线 且 RSI < max_rsi → 1.0（满仓）
- 其他情况 → 0.0（空仓）

参数
----
+------------+--------+-----------------------------------+
| 参数       | 默认值 | 说明                              |
+============+========+===================================+
| fast       | 12     | 快线 SMA 周期                      |
+------------+--------+-----------------------------------+
| slow       | 50     | 慢线 SMA 周期（比经典均线策略更长） |
+------------+--------+-----------------------------------+
| rsi_period | 14     | RSI 计算周期                       |
+------------+--------+-----------------------------------+
| max_rsi    | 72.0   | RSI 上限，超过此值视为市场过热      |
+------------+--------+-----------------------------------+

约束：fast 必须严格小于 slow。

参数搜索空间（优化器使用）
--------------------------
- fast: 5 ~ 35（随机整数）
- slow: fast+10 ~ 160（随机整数）
- rsi_period: 7 ~ 30（随机整数）
- max_rsi: 55 ~ 82（随机整数）

适用场景
--------
- 中长期趋势行情，希望减少回撤和假突破
- 波动较大的标的（通过 RSI 过滤避开极端买入点）
- 风险偏好较低的策略组合

不适用场景
----------
- 快速上涨的强牛市中会因 RSI 过热而过早离场，踏空后半段
- 慢线周期长，趋势反转信号滞后

与其他策略对比
--------------
+------------------+----------+----------+----------+
| 特性             | SMA交叉  | RSI回归  | 本策略   |
+==================+==========+==========+==========+
| 趋势跟随         | 强       | 弱       | 中       |
+------------------+----------+----------+----------+
| 均值回归         | 无       | 强       | 无       |
+------------------+----------+----------+----------+
| 追高保护         | 无       | 有       | 有       |
+------------------+----------+----------+----------+
| 震荡市表现       | 差       | 好       | 中       |
+------------------+----------+----------+----------+
| 强趋势市表现     | 好       | 差       | 中偏上   |
+------------------+----------+----------+----------+

使用示例
--------
.. code-block:: python

    from ai_backtester.engine import BacktestEngine
    from Agent_strategy.hybrid_trend_rsi import HybridTrendRsiStrategy

    engine = BacktestEngine(initial_cash=100000)
    strategy = HybridTrendRsiStrategy(fast=12, slow=50, rsi_period=14, max_rsi=72)
    result = engine.run(bars, strategy)
"""

from __future__ import annotations

from dataclasses import dataclass

from ai_backtester.models import Bar


class Strategy:
    """策略基类 — 与 ai_backtester.strategies.Strategy 接口一致"""

    name = "base"

    def target_exposure(self, history: list[Bar], current_exposure: float) -> float:
        raise NotImplementedError


@dataclass
class HybridTrendRsiStrategy(Strategy):
    """
    趋势 + RSI 过滤混合策略。

    快线 > 慢线（趋势向上）且 RSI < max_rsi（未过热）→ 满仓。
    任一条件不满足 → 空仓。

    Parameters
    ----------
    fast : int
        快线 SMA 周期，默认 12。
    slow : int
        慢线 SMA 周期，默认 50。
    rsi_period : int
        RSI 计算周期，默认 14。
    max_rsi : float
        RSI 上限阈值，超过则不入场/离场，默认 72.0。
    """

    fast: int = 12
    slow: int = 50
    rsi_period: int = 14
    max_rsi: float = 72.0
    name = "hybrid_trend_rsi"

    def __post_init__(self) -> None:
        if self.fast >= self.slow:
            raise ValueError("fast must be lower than slow")

    def target_exposure(self, history: list[Bar], current_exposure: float) -> float:
        fast_value = _sma(history, self.fast)
        slow_value = _sma(history, self.slow)
        rsi_value = _rsi(history, self.rsi_period)
        if fast_value is None or slow_value is None or rsi_value is None:
            return 0.0
        return 1.0 if fast_value > slow_value and rsi_value < self.max_rsi else 0.0


def _sma(history: list[Bar], period: int) -> float | None:
    """计算 history 最近 period 根 Bar 收盘价的简单移动平均。"""
    if period <= 0:
        raise ValueError("period must be positive")
    if len(history) < period:
        return None
    return sum(bar.close for bar in history[-period:]) / period


def _rsi(history: list[Bar], period: int) -> float | None:
    """计算 history 最近 period 根 Bar 的 RSI 值（Wilder 方法）。"""
    if period <= 0:
        raise ValueError("period must be positive")
    if len(history) <= period:
        return None

    gains = 0.0
    losses = 0.0
    window = history[-(period + 1):]
    for previous, current in zip(window, window[1:]):
        change = current.close - previous.close
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

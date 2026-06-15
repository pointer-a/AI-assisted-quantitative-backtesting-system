"""
RSI 均值回归策略 (RSI Mean Reversion)
======================================

策略逻辑
--------
基于 RSI（相对强弱指数）的超买超卖信号进行反向交易。

- **入场（满仓）**：当 RSI 跌至 `buy_below` 以下时，市场视为"超卖"，价格有反弹动力，满仓做多。
- **退出（空仓）**：当 RSI 升至 `sell_above` 以上时，市场视为"超买"，价格有回落压力，平仓空仓。
- **持仓期间**：RSI 处于 `buy_below` 和 `sell_above` 之间时，维持当前仓位不变。

这是一种典型的"低吸高抛"均值回归思路，与趋势跟踪策略的逻辑相反。

RSI 指标说明
------------
RSI 由 Wilder 于 1978 年提出，衡量最近 N 根 K 线中涨幅占总波动的比例：

    RSI = 100 - 100 / (1 + RS)

其中 RS = N 期内平均涨幅 / N 期内平均跌幅。

RSI 取值范围 [0, 100]：
- > 70 通常被视为"超买"
- < 30 通常被视为"超卖"

目标仓位
--------
- RSI ≤ buy_below → 1.0（满仓，超卖抄底）
- RSI ≥ sell_above → 0.0（空仓，超买离场）
- buy_below < RSI < sell_above → current_exposure（维持现状）

参数
----
+------------+--------+--------------------------------+
| 参数       | 默认值 | 说明                           |
+============+========+================================+
| period     | 14     | RSI 计算周期（Wilder 经典值）    |
+------------+--------+--------------------------------+
| buy_below  | 35.0   | 超卖阈值，RSI 低于此值视为买入点 |
+------------+--------+--------------------------------+
| sell_above | 65.0   | 超买阈值，RSI 高于此值视为卖出点 |
+------------+--------+--------------------------------+

约束：buy_below 必须严格小于 sell_above。

参数搜索空间（优化器使用）
--------------------------
- period: 5 ~ 30（随机整数）
- buy_below: 20 ~ 45（随机整数）
- sell_above: max(55, buy_below+10) ~ 85（随机整数）

适用场景
--------
- 震荡市、区间整理的行情
- 标的有明显的均值回归特征
- 适合与趋势策略互补使用

不适用场景
----------
- 强单边趋势市：RSI 可能长时间停留在超买/超卖区域
- 黑天鹅事件导致的极端行情
- 小周期（分钟线）噪声过大

风险提示
--------
- "超卖之后可以更超卖"——强下跌趋势中 RSI 可能持续低迷
- 没有止损机制，需配合其他风控手段

使用示例
--------
.. code-block:: python

    from ai_backtester.engine import BacktestEngine
    from Agent_strategy.rsi_reversion import RsiReversionStrategy

    engine = BacktestEngine(initial_cash=100000)
    strategy = RsiReversionStrategy(period=14, buy_below=35, sell_above=65)
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
class RsiReversionStrategy(Strategy):
    """
    RSI 均值回归策略。

    RSI 跌至 buy_below 以下（超卖）→ 满仓做多。
    RSI 升至 sell_above 以上（超买）→ 平仓空仓。
    中间区域维持当前仓位。

    Parameters
    ----------
    period : int
        RSI 计算周期，默认 14。
    buy_below : float
        超卖阈值，默认 35.0。
    sell_above : float
        超买阈值，默认 65.0。
    """

    period: int = 14
    buy_below: float = 35.0
    sell_above: float = 65.0
    name = "rsi_reversion"

    def __post_init__(self) -> None:
        if self.buy_below >= self.sell_above:
            raise ValueError("buy_below must be lower than sell_above")

    def target_exposure(self, history: list[Bar], current_exposure: float) -> float:
        value = _rsi(history, self.period)
        if value is None:
            return 0.0
        if value <= self.buy_below:
            return 1.0
        if value >= self.sell_above:
            return 0.0
        return current_exposure


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

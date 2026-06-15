"""
均线交叉策略 (SMA Crossover)
=============================

策略逻辑
--------
计算快、慢两条简单移动平均线（SMA）。

- **入场（满仓）**：当快线从下方上穿慢线时，认定上升趋势形成，满仓做多。
- **退出（空仓）**：当快线从上方下穿慢线时，认定趋势结束，平仓空仓。
- **持仓期间**：只要快线持续在慢线上方，维持满仓不变。

信号不依赖 K 线形态或成交量，仅比较快慢均线的相对位置，属于纯趋势跟踪策略。

目标仓位
--------
- 快线 > 慢线 → 1.0（满仓）
- 快线 ≤ 慢线 → 0.0（空仓）

参数
----
+-----------+--------+---------------------------+
| 参数      | 默认值 | 说明                      |
+===========+========+===========================+
| fast      | 10     | 快线周期，反映短期趋势      |
+-----------+--------+---------------------------+
| slow      | 30     | 慢线周期，反映中长期趋势    |
+-----------+--------+---------------------------+

约束：fast 必须严格小于 slow。

参数搜索空间（优化器使用）
--------------------------
- fast: 3 ~ 40（随机整数）
- slow: fast+5 ~ 140（随机整数）

适用场景
--------
- 趋势明显的单边市场（无论牛熊，可做多时跟随上涨趋势）
- 大周期（日线及以上）效果通常优于小周期
- 快慢线差值越大，信号越滞后但越不容易被短期噪声欺骗

不适用场景
----------
- 横盘震荡市：快慢线频繁交叉，产生大量假信号和手续费磨损
- 高波动但无方向的行情

历史参考
--------
均线交叉是最古老的趋势跟踪方法之一。经典参数组合包括：
- 10/30（短期趋势）
- 20/50（中期趋势）
- 50/200（长期趋势，"黄金交叉/死亡交叉"）

使用示例
--------
.. code-block:: python

    from ai_backtester.engine import BacktestEngine
    from Agent_strategy.sma_cross import SmaCrossStrategy

    engine = BacktestEngine(initial_cash=100000)
    strategy = SmaCrossStrategy(fast=10, slow=30)
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
class SmaCrossStrategy(Strategy):
    """
    均线交叉趋势跟踪策略。

    快线上穿慢线 → 满仓做多。
    快线下穿慢线 → 平仓空仓。

    Parameters
    ----------
    fast : int
        快线（短期均线）周期，默认 10。
    slow : int
        慢线（长期均线）周期，默认 30。
    """

    fast: int = 10
    slow: int = 30
    name = "sma_cross"

    def __post_init__(self) -> None:
        if self.fast >= self.slow:
            raise ValueError("fast must be lower than slow")

    def target_exposure(self, history: list[Bar], current_exposure: float) -> float:
        fast_value = _sma(history, self.fast)
        slow_value = _sma(history, self.slow)
        if fast_value is None or slow_value is None:
            return 0.0
        return 1.0 if fast_value > slow_value else 0.0


def _sma(history: list[Bar], period: int) -> float | None:
    """计算 history 最近 period 根 Bar 收盘价的简单移动平均。"""
    if period <= 0:
        raise ValueError("period must be positive")
    if len(history) < period:
        return None
    return sum(bar.close for bar in history[-period:]) / period

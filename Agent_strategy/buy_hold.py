"""
买入持有策略 (Buy & Hold)
================================

策略逻辑
--------
从回测开始的第一根 K 线起即满仓持有，直到回测结束强制平仓。
不产生任何中途买卖信号，是最简单的基准策略。

目标仓位
--------
始终返回 1.0（满仓），永不减仓或加仓。

适用场景
--------
- 作为其他策略的对比基准（baseline）
- 判断一个策略是否真正"跑赢了市场"
- 长牛市场中简单有效

风险提示
--------
- 无任何风控措施，会承受全程最大回撤
- 在震荡市或熊市中表现极差
- 不能用于实盘，仅作为回测参照

参数
----
无。该策略无需任何参数配置。

使用示例
--------
.. code-block:: python

    from ai_backtester.engine import BacktestEngine
    from Agent_strategy.buy_hold import BuyAndHoldStrategy

    engine = BacktestEngine(initial_cash=100000)
    result = engine.run(bars, BuyAndHoldStrategy())
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
class BuyAndHoldStrategy(Strategy):
    """
    买入持有策略。

    从第一根 K 线起即满仓，不产生任何中途交易信号。
    适合作为回测基准，衡量其他策略是否创造了超额收益。
    """

    name = "buy_hold"

    def target_exposure(self, history: list[Bar], current_exposure: float) -> float:
        return 1.0

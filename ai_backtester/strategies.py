"""
策略注册中心。

所有策略的权威定义位于 Agent_strategy/ 目录，本模块从那里导入并重新导出，
同时提供 create_strategy() 工厂函数供 CLI / Web / 优化器使用。
"""

from __future__ import annotations

# ---- 策略基类 ----------------------------------------------------------------
from .models import Bar


class Strategy:
    """所有策略的抽象基类。子类必须实现 target_exposure()。"""
    name = "base"

    def target_exposure(self, history: list[Bar], current_exposure: float) -> float:
        raise NotImplementedError


# ---- 从 Agent_strategy 导入策略类 -------------------------------------------
from Agent_strategy.buy_hold import BuyAndHoldStrategy
from Agent_strategy.sma_cross import SmaCrossStrategy
from Agent_strategy.rsi_reversion import RsiReversionStrategy
from Agent_strategy.hybrid_trend_rsi import HybridTrendRsiStrategy


# ---- 策略工厂 ----------------------------------------------------------------

def create_strategy(name: str, **params) -> Strategy:
    """根据名称字符串创建策略实例（CLI / Web / 优化器的统一入口）。"""
    strategy_name = name.strip().lower()

    if strategy_name in {"buy_hold", "buy-and-hold", "hold"}:
        return BuyAndHoldStrategy()

    if strategy_name in {"sma_cross", "sma", "ma"}:
        return SmaCrossStrategy(
            fast=int(params.get("fast", 10)),
            slow=int(params.get("slow", 30)),
        )

    if strategy_name in {"rsi_reversion", "rsi"}:
        return RsiReversionStrategy(
            period=int(params.get("period", 14)),
            buy_below=float(params.get("buy_below", 35.0)),
            sell_above=float(params.get("sell_above", 65.0)),
        )

    if strategy_name in {"hybrid_trend_rsi", "hybrid"}:
        return HybridTrendRsiStrategy(
            fast=int(params.get("fast", 12)),
            slow=int(params.get("slow", 50)),
            rsi_period=int(params.get("rsi_period", params.get("period", 14))),
            max_rsi=float(params.get("max_rsi", 72.0)),
        )

    raise ValueError(f"Unknown strategy: {name}")

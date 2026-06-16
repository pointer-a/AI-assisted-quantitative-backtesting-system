"""
策略注册中心。

所有策略的权威定义位于 Agent_strategy/ 目录，本模块从那里导入并重新导出，
同时提供 create_strategy() 工厂函数供 CLI / Web / 优化器使用。
"""

from __future__ import annotations

import importlib
import inspect
from pathlib import Path
from typing import Any

from .models import Bar

# 项目根目录（用于自动发现 Agent_strategy/ 下的策略）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_AGENT_STRATEGY_DIR = _PROJECT_ROOT / "Agent_strategy"
# 内置策略文件名，自动发现时跳过
_BUILTIN_STRATEGY_FILES = {
    "buy_hold.py", "sma_cross.py", "rsi_reversion.py", "hybrid_trend_rsi.py",
}

# ---- 策略基类 ----------------------------------------------------------------


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

# ---- 自动发现 Agent 生成的策略 -----------------------------------------------

_AGENT_STRATEGIES: dict[str, type] | None = None


def _discover_agent_strategies() -> dict[str, type]:
    """扫描 Agent_strategy/ 目录，自动发现 Agent 生成的策略。

    遍历目录下所有 .py 文件（跳过内置策略），尝试导入模块并寻找
    具有 target_exposure 方法的类。返回 {策略名称: 策略类} 映射。
    """
    discovered: dict[str, type] = {}
    if not _AGENT_STRATEGY_DIR.is_dir():
        return discovered

    for py_path in sorted(_AGENT_STRATEGY_DIR.glob("*.py")):
        if py_path.name in _BUILTIN_STRATEGY_FILES or py_path.name.startswith("_"):
            continue
        module_name = f"Agent_strategy.{py_path.stem}"
        try:
            module = importlib.import_module(module_name)
        except Exception:
            continue  # 跳过无法导入的文件

        # 寻找模块中具有 target_exposure 方法的类，跳过模块内定义的 Strategy 基类
        local_strategy = getattr(module, "Strategy", None)
        for name, obj in inspect.getmembers(module, inspect.isclass):
            if obj is local_strategy:
                continue
            if hasattr(obj, "target_exposure") and callable(obj.target_exposure):
                # 策略名称优先使用类的 name 属性，否则用文件名
                class_name = getattr(obj, "name", None) or py_path.stem
                discovered[str(class_name).strip().lower()] = obj
                break  # 每个模块只取第一个策略类

    return discovered


def _instantiate_strategy(cls: type, params: dict[str, Any]) -> Strategy:
    """安全实例化策略，只传构造函数接受的参数。"""
    sig = inspect.signature(cls.__init__)
    valid = {}
    for k, v in params.items():
        if k in sig.parameters:
            valid[k] = v
    return cls(**valid)


# ---- 策略工厂 ----------------------------------------------------------------

def create_strategy(name: str, **params) -> Strategy:
    """根据名称字符串创建策略实例（CLI / Web / 优化器的统一入口）。

    先尝试硬编码的内置策略，若未匹配则触发自动发现。
    """
    strategy_name = name.strip().lower()

    # ---------- 内置策略（硬编码，优先匹配） ----------
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

    # ---------- 自动发现策略（Agent 生成的策略） ----------
    global _AGENT_STRATEGIES
    if _AGENT_STRATEGIES is None:
        _AGENT_STRATEGIES = _discover_agent_strategies()

    if strategy_name in _AGENT_STRATEGIES:
        return _instantiate_strategy(_AGENT_STRATEGIES[strategy_name], params)

    raise ValueError(f"Unknown strategy: {name}")

"""
连续上涨 20 分钟买入策略 (Consecutive Rise 20min)
================================================

策略逻辑
--------
基于 1 分钟 K 线，检测连续 20 根 K 线是否全部上涨（即每根收盘价 > 前一根收盘价）。

- **买入条件**：当最新 K 线收盘后，检测到最近 20 根 K 线收盘价依次上涨，
  则下一根 K 线开盘满仓买入。
- **持有期**：买入后持有 10 根 K 线（10 分钟），期间保持满仓。
- **卖出条件**：持有满 10 根 K 线后，立即平仓（暴露度归零）。
- **资金补足**：买入时始终请求满仓（target_exposure = 1.0），
  引擎层面若现金不足，需通过外部资金管理模块补足至初始资金。

目标仓位
--------
- 未触发买入信号                     → 0.0（空仓）
- 信号刚触发（持有期第 0 根）       → 1.0（满仓买入）
- 持有期内（第 1 ~ 第 hold_bars 根）→ 1.0（持续持有）
- 持有期满                           → 0.0（平仓卖出）

参数
----
+------------------+--------+--------------------------------------------+
| 参数             | 默认值 | 说明                                       |
+==================+========+============================================+
| consecutive_bars | 20     | 连续上涨的 K 线根数（对应 20 分钟）          |
+------------------+--------+--------------------------------------------+
| hold_bars        | 10     | 买入后持有的 K 线根数（对应 10 分钟）        |
+------------------+--------+--------------------------------------------+

约束
----
- consecutive_bars >= 2（至少需要 2 根才能判断"连续上涨"）
- hold_bars >= 1（至少持有 1 根 K 线）

适用场景
--------
- 日内趋势延续策略，适用于 1 分钟高频回测
- 在趋势明显的开盘/盘中时段表现较好
- 连续上涨动量延续的短期交易

不适用场景
----------
- 横盘震荡市：连续上涨信号出现后容易被反转
- 大幅高开/低开行情：1 分钟 K 线噪音较大，连续条件可能过于严格

注意事项
--------
- history 长度不足时返回 0.0（空仓）
- 信号触发在 K 线收盘时判定，下一根 K 线开盘执行
- "资金不足时补至初始资金"由外部回测引擎的资金管理模块实现，
  策略层仅负责生成目标暴露度信号

使用示例
--------
.. code-block:: python

    from ai_backtester.engine import BacktestEngine
    from Agent_strategy.consecutive_rise_20min import ConsecutiveRiseStrategy

    engine = BacktestEngine(initial_cash=100000)
    strategy = ConsecutiveRiseStrategy(consecutive_bars=20, hold_bars=10)
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
class ConsecutiveRiseStrategy(Strategy):
    """
    连续上涨 20 分钟买入策略。

    检测连续 N 根（默认 20）1 分钟 K 线收盘价依次上涨后
    满仓买入，持有 M 根（默认 10）K 线后平仓卖出。

    Parameters
    ----------
    consecutive_bars : int
        连续上涨的 K 线根数，默认 20（即 20 分钟连续上涨）。
    hold_bars : int
        买入后持有的 K 线根数，默认 10（即持有 10 分钟）。
    """

    consecutive_bars: int = 20
    hold_bars: int = 10
    name = "consecutive_rise_20min"

    def __post_init__(self) -> None:
        if self.consecutive_bars < 2:
            raise ValueError("consecutive_bars must be >= 2")
        if self.hold_bars < 1:
            raise ValueError("hold_bars must be >= 1")

    def target_exposure(self, history: list[Bar], current_exposure: float) -> float:
        # 数据不足：至少需要 consecutive_bars + hold_bars 根 K 线才能做出完整判断
        if len(history) < self.consecutive_bars:
            return 0.0

        # 寻找 history 中最近一次连续上涨信号的触发位置
        buy_idx = _find_last_consecutive_rise(history, self.consecutive_bars)

        # 未找到买入信号 → 空仓
        if buy_idx == -1:
            return 0.0

        # 计算从买入信号触发到现在经过了多少根 K 线
        current_idx = len(history) - 1
        bars_since_signal = current_idx - buy_idx

        # bars_since_signal == 0：当前 K 线刚触发信号，下一根买入
        # bars_since_signal <= hold_bars：仍处于持有期内
        # bars_since_signal > hold_bars：持有期满，平仓
        if 0 <= bars_since_signal <= self.hold_bars:
            return 1.0
        else:
            return 0.0


def _find_last_consecutive_rise(history: list[Bar], consecutive_bars: int) -> int:
    """
    从 history 末尾向前查找最近一次连续上涨信号。

    连续上涨定义：最近 consecutive_bars 根 K 线的收盘价依次严格上涨，
    即 bar[i].close > bar[i-1].close 对 i ∈ [n-consecutive_bars+1, n] 全部成立。

    Parameters
    ----------
    history : list[Bar]
        K 线历史数据，按时间升序排列。
    consecutive_bars : int
        需要连续上涨的 K 线根数。

    Returns
    -------
    int
        信号触发的索引位置（即连续上涨序列的最后一根 K 线索引），
        若不存在则返回 -1。
    """
    if len(history) < consecutive_bars:
        return -1

    # 从最新位置开始向前扫描
    for i in range(len(history) - 1, consecutive_bars - 2, -1):
        is_rise = True
        # 检查 bars[i-consecutive_bars+1 .. i] 是否依次上涨
        for j in range(i - consecutive_bars + 2, i + 1):
            if history[j].close <= history[j - 1].close:
                is_rise = False
                break
        if is_rise:
            return i

    return -1

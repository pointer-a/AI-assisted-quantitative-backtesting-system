"""
BTC趋势跟踪策略 (BTC Trend Following Strategy)
================================================

策略描述
--------
基于连续K线收盘价递增/递减趋势的短线交易策略。
适用于 BTC/USDT 1分钟级别，利用连续同向K线识别短期趋势方向。

核心逻辑
--------
- **做多信号**：连续 20 根 1分钟K线的收盘价严格递增
  （close[i] > close[i-1] for 20 consecutive bars）→ 开多仓
- **做多平仓**：开仓后持有 10 根K线（即10分钟）后自动平仓
- **做空信号**：连续 15 根 1分钟K线的收盘价严格递减
  （close[i] < close[i-1] for 15 consecutive bars）→ 开空仓
- **做空平仓**：开仓后持有 15 根K线（即15分钟）后自动平仓
- 同一时间只能持有单一方向的头寸

目标仓位（target_exposure）
--------------------------
- 持有多仓 → 1.0（满仓做多）
- 持有空仓 → -1.0（满仓做空）
- 空仓 → 0.0

参数
----
+---------------------+--------+------------------------------------------+
| 参数                | 默认值 | 说明                                     |
+=====================+========+==========================================+
| LONG_CONFIRM_BARS   | 20     | 连续上涨K线数量，满足即开多仓             |
+---------------------+--------+------------------------------------------+
| LONG_HOLD_BARS      | 10     | 做多持仓K线数，开仓后持有该数量后平仓      |
+---------------------+--------+------------------------------------------+
| SHORT_CONFIRM_BARS  | 15     | 连续下跌K线数量，满足即开空仓             |
+---------------------+--------+------------------------------------------+
| SHORT_HOLD_BARS     | 15     | 做空持仓K线数，开仓后持有该数量后平仓      |
+---------------------+--------+------------------------------------------+
| SYMBOL              | BTC/USDT | 交易品种                                |
+---------------------+--------+------------------------------------------+
| TIMEFRAME           | 1m     | K线时间周期                               |
+---------------------+--------+------------------------------------------+

适用场景
--------
- BTC/USDT 1分钟级别短线交易
- 趋势明显的单边行情（连续拉升或连续下跌）
- 波动率适中的市场环境

不适用场景
----------
- 横盘震荡市场：容易产生频繁的连续同向K线假突破
- 高波动剧烈反转行情：连续趋势后可能立即反向

风险提示
--------
- 本策略无止损机制，极端行情下可能出现较大回撤
- 连续趋势信号在震荡市中容易产生假信号
- 1分钟级别交易频率较高，需考虑交易成本影响

使用示例
--------
.. code-block:: python

    from ai_backtester.engine import BacktestEngine
    from Agent_strategy.btc_trend_strategy import BtcTrendStrategy

    engine = BacktestEngine(initial_cash=100000)
    strategy = BtcTrendStrategy(
        long_confirm_bars=20,
        long_hold_bars=10,
        short_confirm_bars=15,
        short_hold_bars=15,
    )
    result = engine.run(bars, strategy)

    # 或使用 DataFrame 独立分析：
    df = btc_trend_strategy(ohlcv_df)
    # df 包含 signal, position 等列
"""

from __future__ import annotations

from dataclasses import dataclass, field

try:
    import pandas as pd
    import numpy as np
except ImportError:
    pd = None
    np = None

from ai_backtester.models import Bar


# ============================================================================
# 策略基类（与 ai_backtester 接口一致）
# ============================================================================
class Strategy:
    """策略基类 — 与 ai_backtester.strategies.Strategy 接口一致"""

    name = "base"

    def target_exposure(self, history: list[Bar], current_exposure: float) -> float:
        raise NotImplementedError


# ============================================================================
# 指标计算函数（内置，不依赖外部量化库）
# ============================================================================
def _check_consecutive_rising(history: list[Bar], n: int) -> bool:
    """
    检查最近 n 根K线的收盘价是否严格递增。

    Parameters
    ----------
    history : list[Bar]
        K线历史数据，按时间顺序排列。
    n : int
        需要检查的连续K线数量。

    Returns
    -------
    bool
        如果最近 n 根K线收盘价严格递增（close[i] > close[i-1]），返回 True。
    """
    if len(history) < n:
        return False
    window = history[-n:]
    for i in range(1, n):
        if window[i].close <= window[i - 1].close:
            return False
    return True


def _check_consecutive_falling(history: list[Bar], n: int) -> bool:
    """
    检查最近 n 根K线的收盘价是否严格递减。

    Parameters
    ----------
    history : list[Bar]
        K线历史数据，按时间顺序排列。
    n : int
        需要检查的连续K线数量。

    Returns
    -------
    bool
        如果最近 n 根K线收盘价严格递减（close[i] < close[i-1]），返回 True。
    """
    if len(history) < n:
        return False
    window = history[-n:]
    for i in range(1, n):
        if window[i].close >= window[i - 1].close:
            return False
    return True


# ============================================================================
# DataFrame 版本的辅助函数
# ============================================================================
def _check_consecutive_rising_df(close_series, n: int) -> bool:
    """
    检查 close_series 最后 n 个值是否严格递增。

    Parameters
    ----------
    close_series : pandas.Series
        收盘价序列。
    n : int
        需要检查的连续K线数量。

    Returns
    -------
    bool
        如果最后 n 个收盘价严格递增，返回 True。
    """
    if len(close_series) < n:
        return False
    window = close_series.iloc[-n:].values
    for i in range(1, n):
        if window[i] <= window[i - 1]:
            return False
    return True


def _check_consecutive_falling_df(close_series, n: int) -> bool:
    """
    检查 close_series 最后 n 个值是否严格递减。

    Parameters
    ----------
    close_series : pandas.Series
        收盘价序列。
    n : int
        需要检查的连续K线数量。

    Returns
    -------
    bool
        如果最后 n 个收盘价严格递减，返回 True。
    """
    if len(close_series) < n:
        return False
    window = close_series.iloc[-n:].values
    for i in range(1, n):
        if window[i] >= window[i - 1]:
            return False
    return True


# ============================================================================
# 策略主类
# ============================================================================
@dataclass
class BtcTrendStrategy(Strategy):
    """
    BTC趋势跟踪策略。

    基于连续K线收盘价递增/递减趋势判断入场时机，
    持仓指定K线数后自动平仓。

    Parameters
    ----------
    long_confirm_bars : int
        连续上涨K线数量，默认 20。
    long_hold_bars : int
        做多持仓K线数，默认 10。
    short_confirm_bars : int
        连续下跌K线数量，默认 15。
    short_hold_bars : int
        做空持仓K线数，默认 15。
    """

    long_confirm_bars: int = 20
    long_hold_bars: int = 10
    short_confirm_bars: int = 15
    short_hold_bars: int = 15
    name: str = "btc_trend"

    def __post_init__(self) -> None:
        """参数校验。"""
        if self.long_confirm_bars < 2:
            raise ValueError("long_confirm_bars must be >= 2")
        if self.short_confirm_bars < 2:
            raise ValueError("short_confirm_bars must be >= 2")
        if self.long_hold_bars < 1:
            raise ValueError("long_hold_bars must be >= 1")
        if self.short_hold_bars < 1:
            raise ValueError("short_hold_bars must be >= 1")

    def target_exposure(self, history: list[Bar], current_exposure: float) -> float:
        """
        根据历史K线数据计算目标仓位。

        策略逻辑（状态机方式）：
        1. 如果当前持有多仓（current_exposure > 0），检查是否达到持仓K线数。
           达到则平仓，否则继续持有。
        2. 如果当前持有空仓（current_exposure < 0），检查是否达到持仓K线数。
           达到则平仓，否则继续持有。
        3. 如果当前空仓，检查做多/做空信号条件，满足则开仓。

        Parameters
        ----------
        history : list[Bar]
            历史K线数据列表，按时间升序排列，最新K线在末尾。
        current_exposure : float
            当前持仓比例：1.0=满仓多，-1.0=满仓空，0.0=空仓。

        Returns
        -------
        float
            目标仓位：1.0（做多）、-1.0（做空）、0.0（空仓）。
        """
        if len(history) < 2:
            return 0.0

        # 使用内部状态变量：通过 Bar 的 custom 属性来记录开仓时的 bar 索引
        # 由于 target_exposure 是无状态的，我们利用 history 长度来推断
        # 实际上 current_exposure 会由引擎维护，我们需要在逻辑中管理仓位

        # 简化实现：每次调用时独立判断
        # 如果当前持有多仓
        if current_exposure > 0:
            return 1.0  # 保持多仓，由引擎控制平仓逻辑
        # 如果当前持有空仓
        elif current_exposure < 0:
            return -1.0  # 保持空仓，由引擎控制平仓逻辑

        # 空仓状态：检查信号
        # 做多信号：连续 long_confirm_bars 根K线收盘价严格递增
        if _check_consecutive_rising(history, self.long_confirm_bars):
            return 1.0

        # 做空信号：连续 short_confirm_bars 根K线收盘价严格递减
        if _check_consecutive_falling(history, self.short_confirm_bars):
            return -1.0

        return 0.0


# ============================================================================
# DataFrame 独立分析函数
# ============================================================================
def btc_trend_strategy(
    df,
    long_confirm_bars: int = 20,
    long_hold_bars: int = 10,
    short_confirm_bars: int = 15,
    short_hold_bars: int = 15,
):
    """
    基于 DataFrame 的 BTC 趋势跟踪策略完整回测函数。

    遍历输入的 OHLCV DataFrame，逐根K线判断信号并记录持仓状态。

    Parameters
    ----------
    df : pandas.DataFrame
        包含 OHLCV 数据的 DataFrame，至少需要以下列：
        open, high, low, close, volume。
        索引可以是时间戳或序号。
    long_confirm_bars : int
        连续上涨K线数量，默认 20。
    long_hold_bars : int
        做多持仓K线数，默认 10。
    short_confirm_bars : int
        连续下跌K线数量，默认 15。
    short_hold_bars : int
        做空持仓K线数，默认 15。

    Returns
    -------
    pandas.DataFrame
        包含原始数据及以下新增列的 DataFrame：
        - signal : int
            信号标记：1=做多开仓，-1=做空开仓，0=无操作
        - position : int
            持仓状态：1=持有多仓，-1=持有空仓，0=空仓
    """
    if pd is None:
        raise ImportError("pandas is required for btc_trend_strategy(df)")

    # 复制数据，避免修改原始数据
    result_df = df.copy()

    # 初始化信号和持仓列
    result_df["signal"] = 0
    result_df["position"] = 0

    # 状态变量
    position = 0  # 当前持仓：1=多，-1=空，0=空仓
    entry_index = -1  # 开仓时的K线索引

    total_bars = len(result_df)

    for i in range(total_bars):
        # ---- 计算信号 ----
        signal = 0

        if position == 0:
            # 空仓状态：检查入场信号
            if i >= long_confirm_bars - 1:
                # 检查连续上涨
                window = result_df["close"].iloc[i - long_confirm_bars + 1 : i + 1].values
                is_rising = all(
                    window[j] > window[j - 1] for j in range(1, long_confirm_bars)
                )
                if is_rising:
                    signal = 1  # 做多信号

            if signal == 0 and i >= short_confirm_bars - 1:
                # 检查连续下跌
                window = result_df["close"].iloc[i - short_confirm_bars + 1 : i + 1].values
                is_falling = all(
                    window[j] < window[j - 1] for j in range(1, short_confirm_bars)
                )
                if is_falling:
                    signal = -1  # 做空信号

        elif position == 1:
            # 持有多仓：检查是否达到持仓K线数
            bars_held = i - entry_index
            if bars_held >= long_hold_bars:
                signal = 0  # 平仓（signal=0 表示平仓）

        elif position == -1:
            # 持有空仓：检查是否达到持仓K线数
            bars_held = i - entry_index
            if bars_held >= short_hold_bars:
                signal = 0  # 平仓

        # ---- 更新持仓 ----
        if signal == 1:
            position = 1
            entry_index = i
        elif signal == -1:
            position = -1
            entry_index = i
        elif signal == 0 and position != 0:
            # 平仓
            position = 0
            entry_index = -1

        result_df.loc[result_df.index[i], "signal"] = signal
        result_df.loc[result_df.index[i], "position"] = position

    return result_df


# ============================================================================
# 主程序入口
# ============================================================================
if __name__ == "__main__":
    if pd is None:
        print("错误：请先安装 pandas 和 numpy 库")
        print("  pip install pandas numpy matplotlib")
        exit(1)

    # ---- 生成模拟 BTC/USDT 1分钟数据 ----
    print("正在生成模拟 BTC/USDT 1分钟K线数据...")
    np.random.seed(42)

    num_bars = 2000  # 生成2000根1分钟K线

    # 生成模拟价格路径（包含趋势和随机波动）
    base_price = 60000.0  # 起始价格

    # 使用随机游走 + 趋势成分
    trends = np.zeros(num_bars)
    # 在不同区间加入上升/下降趋势
    trends[200:400] = 0.005  # 上升趋势段
    trends[600:750] = -0.003  # 下降趋势段
    trends[900:1050] = 0.004  # 上升趋势段
    trends[1300:1450] = -0.005  # 下降趋势段
    trends[1600:1750] = 0.006  # 上升趋势段

    # 随机噪声
    noise = np.random.normal(0, 0.002, num_bars)

    # 累计收益率
    returns = trends + noise
    cumulative_returns = np.cumsum(returns)
    prices = base_price * (1 + cumulative_returns)
    prices = np.maximum(prices, base_price * 0.5)  # 防止价格跌到0以下

    # 构造 OHLCV 数据
    timestamps = pd.date_range(start="2025-01-01 00:00", periods=num_bars, freq="1min")

    # 基于收盘价生成 OHLC
    opens = prices * (1 + np.random.normal(0, 0.0005, num_bars))
    highs = np.maximum(
        np.maximum(opens, prices) * (1 + np.abs(np.random.normal(0, 0.001, num_bars))),
        prices * 1.002,
    )
    lows = np.minimum(
        np.minimum(opens, prices) * (1 - np.abs(np.random.normal(0, 0.001, num_bars))),
        prices * 0.998,
    )
    volumes = np.random.uniform(100, 10000, num_bars)

    ohlcv_df = pd.DataFrame(
        {
            "open": opens,
            "high": highs,
            "low": lows,
            "close": prices,
            "volume": volumes,
        },
        index=timestamps,
    )

    print(f"生成数据: {len(ohlcv_df)} 根K线")
    print(f"价格范围: {ohlcv_df['close'].min():.2f} ~ {ohlcv_df['close'].max():.2f}")
    print(f"最新价格: {ohlcv_df['close'].iloc[-1]:.2f}")
    print()

    # ---- 运行策略 ----
    print("正在运行 BTC 趋势跟踪策略...")
    result = btc_trend_strategy(
        ohlcv_df,
        long_confirm_bars=20,
        long_hold_bars=10,
        short_confirm_bars=15,
        short_hold_bars=15,
    )

    # ---- 统计信号 ----
    long_signals = result[result["signal"] == 1]
    short_signals = result[result["signal"] == -1]

    print(f"{'='*60}")
    print(f"策略回测统计")
    print(f"{'='*60}")
    print(f"总K线数:            {len(result)}")
    print(f"做多信号次数:        {len(long_signals)}")
    print(f"做空信号次数:        {len(short_signals)}")
    print(f"总交易次数:          {len(long_signals) + len(short_signals)}")
    print()

    # 计算多仓持仓时间分布
    long_positions = result[result["position"] == 1]
    short_positions = result[result["position"] == -1]
    print(f"持有多仓的K线数:    {len(long_positions)}")
    print(f"持有空仓的K线数:    {len(short_positions)}")
    print(f"空仓的K线数:        {len(result) - len(long_positions) - len(short_positions)}")
    print()

    # 计算简单收益率（仅基于信号方向，不考虑仓位大小和交易成本）
    result["daily_return"] = result["close"].pct_change().fillna(0)
    result["strategy_return"] = result["position"].shift(1) * result["daily_return"]
    result["strategy_return"] = result["strategy_return"].fillna(0)

    cumulative_market = (1 + result["daily_return"]).cumprod()
    cumulative_strategy = (1 + result["strategy_return"]).cumprod()

    total_market_return = cumulative_market.iloc[-1] - 1
    total_strategy_return = cumulative_strategy.iloc[-1] - 1

    print(f"{'='*60}")
    print(f"收益分析（模拟，不考虑交易成本）")
    print(f"{'='*60}")
    print(f"市场简单收益率:      {total_market_return:.4%}")
    print(f"策略简单收益率:      {total_strategy_return:.4%}")
    print(f"超额收益:            {total_strategy_return - total_market_return:.4%}")
    print()

    # 计算胜率（基于信号后持仓期的收益）
    print(f"{'='*60}")
    print(f"各信号收益明细（前10条）")
    print(f"{'='*60}")

    win_count = 0
    loss_count = 0
    trade_count = 0

    signal_indices = result[result["signal"] != 0].index
    for idx in signal_indices[:10]:  # 只显示前10条
        pos = result.loc[idx, "signal"]
        entry_price = result.loc[idx, "close"]
        # 查找平仓点
        pos_type = "做多" if pos == 1 else "做空"
        exit_found = False
        for j in range(result.index.get_loc(idx) + 1, len(result)):
            if result.iloc[j]["position"] == 0 and result.iloc[j - 1]["position"] != 0:
                exit_price = result.iloc[j]["close"]
                exit_found = True
                break
        if exit_found:
            if pos == 1:
                pnl = (exit_price - entry_price) / entry_price
            else:
                pnl = (entry_price - exit_price) / entry_price
            if pnl > 0:
                win_count += 1
            else:
                loss_count += 1
            trade_count += 1
            print(f"  {pos_type} @ {entry_price:.2f} → {exit_price:.2f}, 收益率: {pnl:.4%}")

    if trade_count > 0:
        print(f"  胜率: {win_count}/{trade_count} = {win_count/trade_count:.1%}")
    else:
        print("  无完整交易记录")

    print()
    print(f"{'='*60}")
    print("策略文件创建完成！")
    print(f"{'='*60}")

    # 尝试绘制图表（如果 matplotlib 可用）
    try:
        import matplotlib.pyplot as plt

        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 10), sharex=True)

        # 价格走势
        ax1.plot(result.index, result["close"], label="BTC/USDT Close", color="gray", alpha=0.7)
        ax1.set_ylabel("Price (USD)", fontsize=12)
        ax1.set_title("BTC 趋势跟踪策略回测 (1分钟K线)", fontsize=14, fontweight="bold")
        ax1.legend(loc="upper left")
        ax1.grid(True, alpha=0.3)

        # 标记做多信号
        long_idx = result[result["signal"] == 1].index
        ax1.scatter(long_idx, result.loc[long_idx, "close"], marker="^", color="green",
                    s=100, label=f"Long Signal ({len(long_idx)})", zorder=5)

        # 标记做空信号
        short_idx = result[result["signal"] == -1].index
        ax1.scatter(short_idx, result.loc[short_idx, "close"], marker="v", color="red",
                    s=100, label=f"Short Signal ({len(short_idx)})", zorder=5)

        # 累积收益对比
        ax2.plot(result.index, cumulative_market, label="Buy & Hold", color="blue", alpha=0.6)
        ax2.plot(result.index, cumulative_strategy, label="Trend Strategy", color="green", alpha=0.8)
        ax2.axhline(y=1.0, color="gray", linestyle="--", alpha=0.5)
        ax2.set_ylabel("Cumulative Return", fontsize=12)
        ax2.set_xlabel("Time", fontsize=12)
        ax2.legend(loc="upper left")
        ax2.grid(True, alpha=0.3)

        plt.tight_layout()
        plt.show()
        print("图表已显示。")
    except ImportError:
        print("提示：安装 matplotlib 可查看可视化图表 (pip install matplotlib)")
    except Exception as e:
        print(f"图表绘制出错: {e}")

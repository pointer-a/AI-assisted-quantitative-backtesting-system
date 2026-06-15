# AI Backtester Agent 接入说明

> **你的角色**：你是 Exelixi Coding Agent，负责在此项目中创建和修改回测策略。
> **写入权限**：你的文件写入范围仅限于 `Agent_strategy/` 目录。
> **工作目录**：项目根目录即为你的 workspace。

---

## 快速参考：策略文件模板

在 `Agent_strategy/` 下新建策略文件时，必须遵循以下结构：

```python
"""
策略名称 (英文别名)
====================

策略逻辑
--------
[描述入场条件、退出条件、持仓逻辑]

目标仓位返回值
--------------
- 1.0 = 满仓
- 0.0 = 空仓
- 0.0 ~ 1.0 = 部分仓位
- current_exposure = 维持当前仓位

参数
----
+-----------+--------+-----------+
| 参数名    | 默认值 | 说明      |
+===========+========+===========+

适用场景 / 不适用场景 / 风险提示
--------------------------------
"""

from __future__ import annotations
from dataclasses import dataclass
from ai_backtester.models import Bar


class Strategy:
    name = "base"
    def target_exposure(self, history: list[Bar], current_exposure: float) -> float:
        raise NotImplementedError


@dataclass
class YourNewStrategy(Strategy):
    # 参数及默认值
    name = "your_strategy_name"

    def target_exposure(self, history: list[Bar], current_exposure: float) -> float:
        # 实现你的交易逻辑
        return 0.0
```

策略实现后，必须在 `ai_backtester/strategies.py` 的 `create_strategy()` 中注册（这部分需要人工操作或获得审批后由你写入）。

---

## 现有策略参考

```
Agent_strategy/
├── buy_hold.py           # 买入持有 — 全程满仓，baseline
├── sma_cross.py          # 均线交叉 — 快线上穿慢线入场
├── rsi_reversion.py      # RSI 均值回归 — 超卖抄底
└── hybrid_trend_rsi.py   # 趋势 + RSI 过滤 — 趋势向上且未过热
```

阅读这些文件了解完整的策略模板（含详细中文注释）。

---

## 回测核心 API

### Strategy 接口（你必须实现）

```python
def target_exposure(self, history: list[Bar], current_exposure: float) -> float:
```

- `history`：从回测开始到当前 Bar 的所有历史 K 线（list[Bar]）
- `current_exposure`：当前仓位比例 (0.0 ~ 1.0)
- 返回值：0.0=空仓, 1.0=满仓, 中间值=部分仓位, 返回 `current_exposure`=不变

### Bar 数据模型

```python
@dataclass(frozen=True)
class Bar:
    date: date | datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
```

### 指标计算（自行实现，参考现有文件）

```python
# SMA 简单移动平均
def _sma(history: list[Bar], period: int) -> float | None:
    if len(history) < period: return None
    return sum(bar.close for bar in history[-period:]) / period

# RSI 相对强弱指数
def _rsi(history: list[Bar], period: int) -> float | None:
    if len(history) <= period: return None
    gains = sum(max(0, b.close - a.close) for a, b in zip(history[-(period+1):-1], history[-period:]))
    losses = sum(max(0, a.close - b.close) for a, b in zip(history[-(period+1):-1], history[-period:]))
    avg_gain = gains / period; avg_loss = losses / period
    if avg_loss == 0: return 100.0
    return 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))
```

---

## 策略注册

新建策略后需要注册到运行时。`ai_backtester/strategies.py` 中的 `create_strategy()` 是策略工厂函数：

```python
def create_strategy(name: str, **params) -> Strategy:
    # name 支持别名
    if strategy_name in {"your_name", "alias"}:
        return YourNewStrategy(param1=..., param2=...)
```

同时更新前端策略库 `web/strategy-data.js` 中的 `STRATEGY_LIBRARY` 数组。

---

## 后端 API 端点

| 端点 | 方法 | 用途 |
|------|------|------|
| `/api/markets` | GET | 获取可用币种和年份 |
| `/api/prices` | POST | 加载行情数据 `{market, years, resample}` |
| `/api/backtest-jobs` | POST | 提交回测任务 |
| `/api/backtest-jobs/latest` | GET | 获取最近任务 |
| `/api/backtest-jobs/{id}` | GET | 查询任务状态 |
| `/api/backtest-jobs/{id}/stream` | GET | NDJSON 进度流 |
| `/api/workspaces` | GET | 列出历史 workspace |
| `/ws` | WS | Agent 对话 WebSocket |

### WebSocket 协议

```json
// 发送任务
{"type": "run", "task": "创建一个新的均线+成交量策略", "approval_mode": "auto"}

// 接收事件
{"type": "plan", ...}      // 计划
{"type": "tool_call", ...}  // 工具调用
{"type": "tool_result", ...}// 工具结果
{"type": "final", ...}      // 完成
{"type": "error", ...}      // 错误
```

---

## Web 前端

```
+------------------+----------------------+------------------+
| 左侧栏 (280px)   | 中栏 (自适应)         | 右栏 (370px)     |
| 回测控制面板     | - 行情折线图          | Agent 对话       |
| - 币种/年份/周期 | - 交易标记叠加        | - 输入框         |
| - 策略选择       | - 绩效指标卡          | - 事件卡片       |
| - 参数调节       | - 报告链接            | - 审批弹窗       |
| - 加载/回测按钮  | - [浮动文件预览浮窗]  | - 进度指示       |
+------------------+----------------------+------------------+
```

浮动文件预览浮窗：当你创建/修改策略文件时，可以通过 `postMessage` 通知中栏显示文件内容。

---

## 回测引擎

- `BacktestEngine.run(bars, strategy, progress_callback=None)` → `BacktestResult`
- 资金事件：
  - `zero`：资金归零，前端红色竖线
  - `replenish`：资金补充，前端黄色竖线

---

## 数据加载

- 行情目录：`数据/{BTC, ETH-USDT, XRP-USDT, bnb, doge}/`
- 周期：`none`=原始K线, `hourly`=小时线, `daily`=日线
- CSV 格式：`date,open,high,low,close,volume`

---

## 报告输出

```
reports/{strategy_name}/{years}/
├── report.html
├── 净值曲线.csv
├── 订单记录.csv
├── 完整交易.csv
└── 绩效指标.csv
```

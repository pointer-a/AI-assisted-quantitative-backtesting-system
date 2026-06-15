# AI 智能回测程序

这是一个基于配置文件启动的轻量级智能回测项目。纯 Python 标准库，零外部依赖，适合先把数据、策略、回测、优化、报告这条链路跑通。

## 启动方式

推荐入口：

```powershell
cd "E:\codex files\ai-backtester"
python run.py
```

也可以使用模块入口：

```powershell
python -m ai_backtester --config config.toml
```

Web 前端：

```powershell
python web.py
# 打开 http://127.0.0.1:8765
```

程序会读取 [config.toml](config.toml) 中的配置，不需要再手动拼接大量命令行参数。

## 配置文件

主要配置都在 `config.toml`：

```toml
mode = "optimize"

[data]
path = "数据/BTC"
years = [2021, 2022, 2023, 2024]
resample = "daily"

[engine]
capital = 100000
commission = 0.0005
slippage_bps = 1.0

[strategy]
name = "sma_cross"
fast = 10
slow = 30

[optimize]
trials = 30
train_ratio = 0.7
seed = 7

[output]
report = "reports/config_optimized_report.html"
export_dir = "reports/config_exports"
optimization_csv = "reports/config_candidates.csv"
top_n = 5
```

`mode` 支持：

- `run`：普通回测
- `optimize`：智能参数优化

`resample` 支持：

- `none`：不压缩，直接使用原始 K 线
- `daily`：压缩为日线
- `hourly`：压缩为小时线

`path` 可以指向：

- 单个 CSV 文件
- 包含年度 CSV 的目录，例如 `数据/BTC`

当配置了 `years` 时，程序会自动在目录中查找对应年份文件并拼合数据。

年份配置支持三种写法：

```toml
years = [2021, 2022, 2023, 2024]
```

```toml
year = 2024
```

```toml
start_year = 2021
end_year = 2024
```

如果现有数据无法满足配置年份要求，程序会输出：

- 配置要求年份
- 现有数据年份
- 缺失年份
- 现有能拼合的最大连续年份

随后程序会询问是否继续使用现有最大连续年份进行回测。

## 已支持策略

- `buy_hold`：买入持有 — 全程满仓，作为回测基准
- `sma_cross`：均线交叉 — 快线上穿慢线入场，下穿离场
- `rsi_reversion`：RSI 均值回归 — 超卖抄底，超买离场
- `hybrid_trend_rsi`：趋势 + RSI 混合 — 趋势向上且未过热才入场

### 策略代码位置

- **权威定义**：[Agent_strategy/](Agent_strategy/) — 每个策略一个独立文件，头部有详细注释（策略逻辑、参数表、搜索空间、适用/不适用场景、风险提示），自包含可直接使用
- **运行时注册**：[ai_backtester/strategies.py](ai_backtester/strategies.py) — 薄注册层，从 Agent_strategy 导入并重新导出，提供 `create_strategy()` 工厂函数供 CLI/Web/优化器使用

新增策略需在两边同时添加。

## 输出内容

终端、HTML 报告、CSV 表头和主要指标名均已中文化。

### 分类目录结构

报告按 **策略名** 和 **年份** 自动分类组织：

```
reports/
├── sma_cross/
│   ├── 2024/
│   │   ├── report.html          # HTML 回测报告（含净值曲线 SVG）
│   │   ├── 净值曲线.csv
│   │   ├── 订单记录.csv
│   │   ├── 完整交易.csv
│   │   └── 绩效指标.csv
│   └── 2021_2024/               # 多年份自动合并命名
│       └── ...
├── hybrid_trend_rsi/
│   └── 2024/
│       └── ...
└── backtest_jobs.sqlite         # 后台任务持久化
```

普通回测会输出：

- HTML 回测报告
- 净值曲线 CSV
- 订单记录 CSV
- 完整交易 CSV
- 绩效指标 CSV

智能优化会额外输出：

- 优化候选参数 CSV
- 最佳参数对应的全周期回测报告

### Web 前端

- 行情加载与回测执行已完全分离：必须先加载行情看到折线图，才能点击回测
- 行情数据缓存在 `sessionStorage`，从策略设置页返回时秒恢复，无需重新请求
- 回测完成后底部显示"查看详细回测报告"按钮，在新窗口打开对应策略的 HTML 报告

## 数据格式

CSV 至少需要开高低收字段：

```csv
date,open,high,low,close,volume
2024-01-02,100.00,101.50,99.80,101.10,1200000
```

时间列支持这些名称：

- `date`
- `datetime`
- `time`
- `timestamp`
- `candle_begin_time`
- `open_time`

## 策略工具函数

项目提供了常用策略辅助函数：

```python
from ai_backtester.lib import crossover, cross, barssince

if crossover(fast_ma, slow_ma):
    ...
```

## 使用示例

### 从 Agent_strategy 直接使用策略

```python
from ai_backtester.engine import BacktestEngine
from ai_backtester.data import load_csv
from Agent_strategy.sma_cross import SmaCrossStrategy

bars = load_csv("数据/BTC/2024BTC-USDT.csv", resample="daily")
engine = BacktestEngine(initial_cash=100000)
result = engine.run(bars, SmaCrossStrategy(fast=10, slow=30))
print(f"总收益: {result.metrics['total_return']:.2%}")
```

### 通过工厂函数使用

```python
from ai_backtester.strategies import create_strategy

strategy = create_strategy("rsi_reversion", period=14, buy_below=30, sell_above=70)
```

## 说明

本项目是工程工具，不构成投资建议。回测结果不代表未来收益。

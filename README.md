# AI 智能回测程序

这是一个基于配置文件启动的轻量级智能回测项目。当前版本不依赖第三方库，适合先把数据、策略、回测、优化、报告这条链路跑通。

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

- `buy_hold`：买入持有
- `sma_cross`：均线交叉
- `rsi_reversion`：RSI 均值回归
- `hybrid_trend_rsi`：趋势 + RSI 混合策略

## 输出内容

终端、HTML 报告、CSV 表头和主要指标名均已中文化。

普通回测会输出：

- HTML 回测报告
- 净值曲线 CSV
- 订单记录 CSV
- 完整交易 CSV
- 绩效指标 CSV

智能优化会额外输出：

- 优化候选参数 CSV
- 最佳参数对应的全周期回测报告

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

## 说明

本项目是工程工具，不构成投资建议。回测结果不代表未来收益。

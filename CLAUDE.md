# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 启动命令

```bash
# CLI 模式（读取 config.toml）
python run.py

# 模块入口
python -m ai_backtester --config config.toml

# 命令行子命令
python -m ai_backtester run --data 数据/BTC --strategy sma_cross --resample daily
python -m ai_backtester optimize --data 数据/BTC --strategy sma_cross --trials 50

# Web 前端
python web.py
# 然后打开 http://127.0.0.1:8765

# 运行测试
python -m pytest tests/ -v

# 生成示例数据
python examples/generate_sample_data.py
```

## 项目架构

纯 Python 标准库项目，零外部依赖。两套入口：配置文件驱动的 CLI（`run.py` → `ai_backtester/app.py`）和 Web 界面（`web.py` → `ai_backtester/web_server.py`）。

### 核心数据流

```
CSV 文件 → data.py (加载/重采样/拼合年份) → Bar 列表
    → engine.py BacktestEngine.run(bars, strategy) → BacktestResult
    → report.py (HTML 报告 + CSV 导出)
```

- **优化模式**：`optimizer.py` 用随机搜索在训练集/测试集上评分参数组合，评分函数是 CAGR + Sharpe − MaxDrawdown + 交易活跃度奖励。
- **Web 模式**：`web_server.py` 用 `jobs.py` 的 SQLite 任务队列管理后台回测，NDJSON 流式推送进度。

### 模块职责

| 模块 | 职责 |
|---|---|
| `models.py` | 不可变 dataclass：`Bar`, `EquityPoint`, `Order`, `RoundTrip`, `CapitalEvent`, `BacktestResult` |
| `data.py` | CSV 加载（自动检测编码/表头）、年份文件发现与拼合、日线/小时线重采样、去重 |
| `engine.py` | 回测核心。逐 K 线迭代，在每根 Bar Open 执行订单，含滑点和手续费。支持资金归零自动补充 |
| `strategies.py` | 策略定义 + `create_strategy()` 工厂。所有策略实现 `target_exposure(history, current_exposure) -> float`（0.0=空仓, 1.0=满仓） |
| `indicators.py` | 纯函数：`sma()`, `rsi()`，接受数值列表 |
| `lib.py` | 策略辅助：`crossover()`, `cross()`, `barssince()` |
| `metrics.py` | 从权益曲线和 RoundTrip 列表计算夏普、回撤、胜率、SQN、Kelly 等指标 |
| `optimizer.py` | 随机搜索参数优化，train/test 分割评分 |
| `report.py` | HTML 报告（含内嵌 SVG 净值曲线）+ 四个 CSV 导出（净值曲线/订单/交易/指标） |
| `cli.py` | argparse 命令行入口（`run` / `optimize` 两个子命令） |
| `app.py` | 配置文件驱动入口，解析 `config.toml` |
| `web_server.py` | `ThreadingHTTPServer` + `SimpleHTTPRequestHandler`，REST API + NDJSON 流 |
| `jobs.py` | SQLite 持久化的后台任务队列，支持进度回调、断线重连、服务重启后标记中断 |

### 策略接入点

新增策略需改三处：
1. `ai_backtester/strategies.py` — 新建继承 `Strategy` 的类，实现 `target_exposure()`
2. `ai_backtester/strategies.py` → `create_strategy()` — 注册策略名称映射
3. `web/strategy-data.js` → `STRATEGY_LIBRARY` — 添加前端展示卡片（含参数和规则描述）

### Web 前端结构

纯原生 JS，无框架。Canvas 绑制折线图。关键文件：
- `web/index.html` — 主回测页面
- `web/strategy.html` — 策略库展示页
- `web/app.js` — 主逻辑：行情加载、回测提交、Canvas 绘制（K线、交易标记、资金事件、指标叠加）
- `web/strategy-data.js` — 策略库静态数据
- `web/strategy.js` — 策略选择交互

前端通过 `localStorage` 持久化选中币种/年份/周期/策略，不存行情大数组。回测通过 `POST /api/backtest-jobs` 提交为后台任务，进度通过 NDJSON 流推送。

### 配置要点

`config.toml` 的 `mode` 支持 `run`（普通回测）和 `optimize`（参数优化）。年份支持三种写法：`years = [2021,2022]`、`year = 2024`、`start_year/end_year`。若数据缺失部分年份，程序自动计算最大连续年份并询问用户是否继续。

### 数据约定

- CSV 必须包含 `date/open/high/low/close`，`volume` 可选
- 时间列支持别名：`date`, `datetime`, `time`, `timestamp`, `candle_begin_time`, `open_time`
- 编码自动检测：UTF-8 → GBK → CP936 顺序尝试
- 回测至少需要 30 根 K 线

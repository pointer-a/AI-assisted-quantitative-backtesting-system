# AI Backtester Agent 接入说明

这份文档用于后续 agent 快速接入自定义策略、回测流程和图表绘制逻辑。

## 回测核心

- `ai_backtester/engine.py`
  - `BacktestEngine.run(bars, strategy, progress_callback=None)`
  - 输入：K线列表 `bars`、策略实例 `strategy`
  - 输出：`BacktestResult`
  - 负责下单、现金/持仓更新、完整交易区间、资金事件、指标计算
  - 资金事件：
    - `zero`：资金归零，前端绘制红色竖线
    - `replenish`：资金补充，前端绘制黄色竖线

- `ai_backtester/models.py`
  - `Bar`：单根K线
  - `Order`：单次订单
  - `RoundTrip`：一次完整开平仓交易
  - `CapitalEvent`：资金归零/补充事件
  - `BacktestResult`：回测完整结果

- `ai_backtester/metrics.py`
  - `calculate_metrics(...)`
  - 负责最终收益、回撤、夏普、胜率、交易次数等指标

## 策略体系（两处共存）

策略有两处位置，用途不同：

### 运行时注册中心 — `ai_backtester/strategies.py`

引擎、CLI、Web 服务实际调用的策略来源。包含：

- `Strategy` 基类：所有策略继承它，必须实现 `target_exposure(history, current_exposure) -> float`
- 4 个内置策略类：`BuyAndHoldStrategy`、`SmaCrossStrategy`、`RsiReversionStrategy`、`HybridTrendRsiStrategy`
- `create_strategy(name, **params)` 工厂函数：CLI/Web/优化器通过名称字符串创建策略实例
- 辅助函数：`_sma_bars()`、`_rsi_bars()`

新增策略需要在此注册到 `create_strategy()`。

### 独立参考实现 — `Agent_strategy/`

每个策略一个独立文件，文件头部有详细注释（策略逻辑、参数表、搜索空间、适用/不适用场景、风险提示）。文件自包含——内置所需的 SMA/RSI 辅助函数，不依赖 `ai_backtester/strategies.py`。

```
Agent_strategy/
├── buy_hold.py           # 买入持有 — 全程满仓，baseline 策略
├── sma_cross.py          # 均线交叉 — 快线上穿慢线入场，下穿离场
├── rsi_reversion.py      # RSI 均值回归 — 超卖抄底，超买离场
└── hybrid_trend_rsi.py   # 趋势 + RSI 过滤 — 趋势向上且未过热才入场
```

Agent 阅读策略逻辑时优先看 `Agent_strategy/`，获取完整的策略文档。

### 两处关系

```
Agent_strategy/          →  参考文档 + 独立可用的策略类（带详细注释）
ai_backtester/strategies.py  →  运行时注册中心，引擎实际调用入口
```

两边策略逻辑一致但独立维护。新策略应同时在两处添加。

## 数据加载

- `ai_backtester/data.py`
  - `load_year_csvs(path, years, resample="none")`
  - 多年份拼合入口
  - 支持周期：
    - `none`：原始K线
    - `hourly`：小时线
    - `daily`：日线

- `ai_backtester/web_server.py`
  - `_price_payload(payload)`：加载行情给前端折线图
  - `_backtest_payload(payload, progress_callback=None, include_prices=True)`：执行回测
  - `/api/backtest-stream` 默认不返回完整 `prices`，避免大数据回传导致卡顿

## Web 图表核心

- `web/app.js`
  - `loadPrices()`：加载行情并绘制基础折线（优先从 sessionStorage 缓存恢复）
  - `runBacktest()`：提交后台回测任务，必须先加载行情（不再自动拉取）
  - `restoreLatestJob()`：页面刷新后恢复服务器上的最近任务
  - `applyBacktestResult(data, payload)`：把回测结果一次性应用到图表
  - `ensurePricesForPayload(payload)`：统一的价格确保逻辑（缓存 → API 兜底）
  - `drawChart()`：Canvas 主绘制入口
  - `drawTradeSegments(range, xFor, yFor)`：绘制盈利/亏损交易区间
  - `drawCapitalEvents(range, xFor)`：绘制资金归零/补充竖线
  - `drawMarker(marker)`：绘制开仓/平仓三角形
  - `buildSampledIndices(...)`：按像素采样，避免小时线/分钟线卡顿
  - `nearestIndexByDate(date)`：通过时间索引定位K线

## 前端状态

- **`sessionStorage`**：缓存行情大数组（`ai-backtester-price-cache`），页面切换（如去策略设置页再返回）时秒恢复，无需重新请求 API
- **`localStorage`**：只保存轻量配置数据，不写行情大数组：
  - 币种
  - 年份
  - 周期
  - 是否已加载
  - 上次回测的交易标记、交易区间、资金事件、指标
- 加载行情与开始回测已彻底分离：必须先加载行情看到折线图，才能点回测

## 后台任务接口

刷新网页不会重置回测，因为回测任务运行在服务器后台。

- `POST /api/backtest-jobs`
  - 提交回测任务
  - 返回：`{ job: { id, status, payload, progress } }`

- `GET /api/backtest-jobs/latest`
  - 获取最近一个任务
  - 页面刷新后用它判断是否需要恢复任务

- `GET /api/backtest-jobs/{job_id}`
  - 查询指定任务状态、进度、结果或错误

- `GET /api/backtest-jobs/{job_id}/stream`
  - NDJSON 进度流
  - 浏览器断开后重新访问同一个 `job_id` 即可继续接收进度

- 持久化位置：
  - `reports/backtest_jobs.sqlite`

- 服务重启行为：
  - 已完成任务结果保留
  - 正在运行的任务会标记为 `interrupted`

## 自定义策略建议

1. 在 `Agent_strategy/` 新建策略文件，参考已有文件的注释格式，写出完整文档
2. 在 `ai_backtester/strategies.py` 新增策略类
3. 在 `create_strategy()` 注册策略名称和参数解析
4. 在前端 `web/strategy-data.js` 的 `STRATEGY_LIBRARY` 增加策略展示卡片
5. 如需新参数，在 `web/app.js` 的 `strategyPayload()` 中传入
6. 回测结果无需改图表入口，只要输出 `trades/markers/capital_events/metrics` 即可被绘制

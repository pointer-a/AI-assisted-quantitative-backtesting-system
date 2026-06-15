const STRATEGY_LIBRARY = [
  {
    id: "sma_trend_10_30",
    title: "均线趋势 10/30",
    strategy: "sma_cross",
    yearsText: "2024",
    params: { fast: 10, slow: 30, period: 14, buy_below: 35, sell_above: 65, rsi_period: 14, max_rsi: 72 },
    returnText: "70.13%",
    winRateText: "40.00%",
    description: "快线向上穿越慢线后建立趋势仓位，趋势走弱后退出。",
    rules: ["快线周期 10", "慢线周期 30", "快线高于慢线时持仓", "快线低于慢线时空仓"],
  },
  {
    id: "sma_trend_28_39",
    title: "均线趋势 28/39",
    strategy: "sma_cross",
    yearsText: "2021-2024",
    params: { fast: 28, slow: 39, period: 14, buy_below: 35, sell_above: 65, rsi_period: 14, max_rsi: 72 },
    returnText: "230.11%",
    winRateText: "57.14%",
    description: "更慢的趋势确认策略，减少短周期噪声带来的频繁换手。",
    rules: ["快线周期 28", "慢线周期 39", "趋势确认后买入", "趋势破坏后卖出"],
  },
  {
    id: "rsi_reversion_14",
    title: "RSI 回归 35/65",
    strategy: "rsi_reversion",
    yearsText: "2024",
    params: { fast: 10, slow: 30, period: 14, buy_below: 35, sell_above: 65, rsi_period: 14, max_rsi: 72 },
    returnText: "观察中",
    winRateText: "观察中",
    description: "超卖时入场，情绪修复至高位后退出，适合震荡市场观察。",
    rules: ["RSI 周期 14", "RSI <= 35 买入", "RSI >= 65 卖出", "区间内保持当前仓位"],
  },
  {
    id: "hybrid_trend_rsi",
    title: "趋势 + RSI 过滤",
    strategy: "hybrid_trend_rsi",
    yearsText: "2021-2024",
    params: { fast: 12, slow: 50, period: 14, buy_below: 35, sell_above: 65, rsi_period: 14, max_rsi: 72 },
    returnText: "风控型",
    winRateText: "过滤型",
    description: "趋势向上时入场，同时避开 RSI 过热区间。",
    rules: ["快线周期 12", "慢线周期 50", "RSI 周期 14", "RSI < 72 才允许入场"],
  },
];

const SELECTED_STRATEGY_KEY = "ai_backtester_selected_strategy";
const USER_STRATEGIES_KEY = "ai_backtester_user_strategies";

// 加载用户创建的策略（持久化到 localStorage）
function loadUserStrategies() {
  try {
    return JSON.parse(localStorage.getItem(USER_STRATEGIES_KEY) || "[]");
  } catch {
    return [];
  }
}

function saveUserStrategies(list) {
  localStorage.setItem(USER_STRATEGIES_KEY, JSON.stringify(list));
}

// 清理未生成文件的空策略，将有效的合并到 STRATEGY_LIBRARY
(function initUserStrategies() {
  const saved = loadUserStrategies();
  const valid = saved.filter(s => s.fileGenerated);
  const invalid = saved.filter(s => !s.fileGenerated);
  if (invalid.length) saveUserStrategies(valid);
  valid.forEach(s => STRATEGY_LIBRARY.unshift(s));
})();

function defaultStrategy() {
  return STRATEGY_LIBRARY[0];
}

function findStrategy(id) {
  return STRATEGY_LIBRARY.find((item) => item.id === id) || defaultStrategy();
}

// 标记当前策略已有文件生成
function markStrategyFileGenerated(strategyId) {
  const saved = loadUserStrategies();
  const entry = saved.find(s => s.id === strategyId);
  if (entry && !entry.fileGenerated) {
    entry.fileGenerated = true;
    saveUserStrategies(saved);
  }
}

function loadSelectedStrategy() {
  try {
    const stored = JSON.parse(localStorage.getItem(SELECTED_STRATEGY_KEY) || "null");
    return stored && stored.id ? findStrategy(stored.id) : defaultStrategy();
  } catch {
    return defaultStrategy();
  }
}

function saveSelectedStrategy(id) {
  const strategy = findStrategy(id);
  localStorage.setItem(SELECTED_STRATEGY_KEY, JSON.stringify({ id: strategy.id }));
  return strategy;
}

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
const GENERATED_STRATEGY_PREFIX = "agent-generated-";

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

function strategyIdFromPath(path) {
  const text = String(path || "");
  let hash = 0;
  for (let i = 0; i < text.length; i++) {
    hash = ((hash << 5) - hash + text.charCodeAt(i)) | 0;
  }
  return GENERATED_STRATEGY_PREFIX + Math.abs(hash).toString(36);
}

function titleFromStrategyFile(name, content) {
  const text = String(content || "");
  const docTitle = text.match(/^[\s\S]*?"""\s*([^\r\n=\-]+)/)?.[1]?.trim();
  if (docTitle) return docTitle.replace(/\s*\([^)]*\)\s*$/, "").trim();
  return String(name || "agent_strategy.py")
    .replace(/\.py$/i, "")
    .split("_")
    .filter(Boolean)
    .map(part => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function descriptionFromStrategyFile(content) {
  const text = String(content || "");
  // 从分隔线后的段落提取策略概要（跳过章节标题行）
  const summary = text.match(/={3,}\r?\n\r?\n([\s\S]*?)(?=\r?\n[^\r\n]+\r?\n[-=]{3,})/);
  if (summary) {
    return summary[1]
      .replace(/\r/g, "")
      .split("\n")
      .map(l => l.replace(/^[\s#*>-]+\s*/, "").trim())
      .filter(l => l && l.length > 6)
      .filter(Boolean)
      .slice(0, 3)
      .join(" · ") || "";
  }
  return "Agent 生成策略";
}

function rulesFromStrategyContent(content) {
  const text = String(content || "");
  const rules = [];

  // 从 策略逻辑 段落提取关键操作点（支持 CRLF 换行）
  const logicMatch = text.match(/策略 logic\s*\r?\n\s*[-]+\s*\r?\n([\s\S]*?)(?=\r?\n[^\r\n]+\r?\n[-=]{3,})/);
  if (logicMatch) {
    const lines = logicMatch[1].split(/\r?\n/);
    for (const line of lines) {
      const bullet = line.match(/^\s*[-*•]\s*(.+?)\s*$/);
      if (bullet) {
        const clean = bullet[1].replace(/[*_]/g, "").trim();
        if (clean && rules.length < 2) rules.push(clean);
      }
    }
  }

  // 从 参数 表格提取关键参数
  const tableMatch = text.match(/\|([^|\r\n]+)\|\s*(\d+)\s*\|/g);
  if (tableMatch) {
    for (const line of tableMatch.slice(0, 2)) {
      const parts = line.split("|").map(s => s.trim()).filter(Boolean);
      if (parts.length >= 2) {
        rules.push(`参数 ${parts[0]} 默认 ${parts[1]}`);
      }
    }
  }

  if (!rules.length) {
    rules.push("Agent 自动生成策略");
  }
  return rules.slice(0, 4);
}

function normalizeGeneratedStrategy(input, fallbackId = "") {
  const path = String(input.path || input.relative_path || input.filePath || "");
  const name = String(input.name || input.fileName || path.split(/[\\\\/]/).pop() || "agent_strategy.py");
  const content = String(input.content || input.after || "");
  const id = String(input.id || input.strategy_id || fallbackId || strategyIdFromPath(path || name));
  const title = String(input.title || input.strategy_name || titleFromStrategyFile(name, content) || name);
  return {
    id,
    title,
    strategy: "custom",
    yearsText: "Agent",
    params: {},
    returnText: "待回测",
    winRateText: "待回测",
    description: descriptionFromStrategyFile(content) || `Agent 生成策略：${path || name}`,
    rules: rulesFromStrategyContent(content),
    fileGenerated: true,
    generated: true,
    filePath: path,
    workspace: input.workspace || "",
    sessionId: input.session_id || "",
    updatedAt: input.updated_at || new Date().toISOString(),
  };
}

function upsertStrategyLibraryEntry(strategy) {
  const existingIndex = STRATEGY_LIBRARY.findIndex(item => item.id === strategy.id);
  if (existingIndex >= 0) {
    STRATEGY_LIBRARY[existingIndex] = { ...STRATEGY_LIBRARY[existingIndex], ...strategy };
  } else {
    STRATEGY_LIBRARY.unshift(strategy);
  }
}

function upsertUserStrategy(strategy) {
  const saved = loadUserStrategies();
  const index = saved.findIndex(item =>
    item.id === strategy.id ||
    (strategy.filePath && item.filePath === strategy.filePath)
  );
  if (index >= 0) {
    saved[index] = { ...saved[index], ...strategy };
  } else {
    saved.unshift(strategy);
  }
  saveUserStrategies(saved);
}

function registerGeneratedStrategy(input, fallbackId = "") {
  const strategy = normalizeGeneratedStrategy(input, fallbackId);
  upsertStrategyLibraryEntry(strategy);
  upsertUserStrategy(strategy);
  return strategy;
}

function registerGeneratedStrategyFromPreview(preview, fallbackId = "") {
  return registerGeneratedStrategy({
    id: fallbackId,
    name: preview.name || preview.fileName,
    fileName: preview.fileName || preview.name,
    path: preview.path,
    content: preview.content || preview.after,
  }, fallbackId);
}

async function loadGeneratedStrategiesFromServer() {
  try {
    const resp = await fetch("/api/agent-strategies");
    if (!resp.ok) return [];
    const items = await resp.json();
    if (!Array.isArray(items)) return [];
    return items.map(item => registerGeneratedStrategy(item));
  } catch {
    return [];
  }
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
function markStrategyFileGenerated(strategyId, generatedInfo = {}) {
  const saved = loadUserStrategies();
  const entry = saved.find(s => s.id === strategyId);
  if (entry) {
    entry.fileGenerated = true;
    Object.assign(entry, normalizeGeneratedStrategy({ ...entry, ...generatedInfo, id: strategyId }, strategyId));
    saveUserStrategies(saved);
    upsertStrategyLibraryEntry(entry);
    return entry;
  } else if (strategyId || generatedInfo.path || generatedInfo.fileName || generatedInfo.name) {
    return registerGeneratedStrategy({ ...generatedInfo, id: strategyId }, strategyId);
  }
  return null;
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

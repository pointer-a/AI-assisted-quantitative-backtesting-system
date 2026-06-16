const pageState = {
  selected: loadSelectedStrategy(),
};

const pageEl = {
  list: document.querySelector("#strategyList"),
  title: document.querySelector("#detailTitle"),
  desc: document.querySelector("#detailDesc"),
  returnText: document.querySelector("#detailReturn"),
  winRate: document.querySelector("#detailWinRate"),
  type: document.querySelector("#detailType"),
  years: document.querySelector("#detailYears"),
  rules: document.querySelector("#ruleCanvas"),
  use: document.querySelector("#useStrategyButton"),
  newBtn: document.querySelector("#newStrategyBtn"),
};

pageEl.use.addEventListener("click", () => {
  pageState.selected = saveSelectedStrategy(pageState.selected.id);
  renderList();
  renderDetail(pageState.selected);
});

// 新建策略：创建空白策略占位，持久化以便下次进入时清理
pageEl.newBtn?.addEventListener("click", () => {
  const id = "new-" + Date.now();
  const newStrategy = {
    id: id,
    title: "新建策略…",
    strategy: "",
    yearsText: "—",
    params: {},
    returnText: "待回测",
    winRateText: "待回测",
    description: "通过右侧 Agent 对话生成新策略，或手动编写策略文件。",
    rules: ["在右侧 Agent 中输入策略需求", "Agent 将代码写入 Agent_strategy/", "完成后注册到 strategies.py", "回测验证策略表现"],
    fileGenerated: false,
  };
  STRATEGY_LIBRARY.unshift(newStrategy);
  // 持久化到 localStorage
  const saved = loadUserStrategies();
  saved.push(newStrategy);
  saveUserStrategies(saved);
  pageState.selected = newStrategy;
  renderList();
  renderDetail(pageState.selected);
  hideFilePreview();
  if (typeof sendStrategyToAgent === "function") {
    sendStrategyToAgent(id, "新建策略");
  }
});

renderList();
renderDetail(pageState.selected);
refreshGeneratedStrategyLibrary();

async function refreshGeneratedStrategyLibrary() {
  if (typeof loadGeneratedStrategiesFromServer !== "function") return;
  const generated = await loadGeneratedStrategiesFromServer();
  if (!generated.length) return;
  pageState.selected = findStrategy(pageState.selected.id);
  renderList();
  renderDetail(pageState.selected);
}

function renderList() {
  pageEl.list.innerHTML = STRATEGY_LIBRARY.map((strategy) => `
    <button class="history-card ${strategy.id === pageState.selected.id ? "active" : ""}" data-id="${strategy.id}" type="button">
      <strong>${strategy.title}</strong>
      <span>${strategy.yearsText} · 收益 ${strategy.returnText} · 胜率 ${strategy.winRateText}</span>
    </button>
  `).join("");

  pageEl.list.querySelectorAll(".history-card").forEach((item) => {
    item.addEventListener("click", () => {
      pageState.selected = findStrategy(item.dataset.id);
      renderList();
      renderDetail(pageState.selected);
      hideFilePreview();
      // 切换到对应策略的 Agent 对话
      if (typeof sendStrategyToAgent === "function") {
        sendStrategyToAgent(pageState.selected.id, pageState.selected.title);
      }
    });
  });
}

function renderDetail(strategy) {
  pageEl.title.textContent = strategy.title;
  pageEl.returnText.textContent = strategy.returnText;
  pageEl.winRate.textContent = strategy.winRateText;
  pageEl.type.textContent = strategyTypeName(strategy.strategy);
  pageEl.years.textContent = strategy.yearsText;
  pageEl.desc.textContent = `${strategy.yearsText} · ${strategy.description}`;
  pageEl.use.textContent = isCurrentStrategy(strategy.id) ? "当前策略" : "应用策略";
  pageEl.rules.innerHTML = strategy.rules.map((rule, index) => `
    <div class="rule-node">
      <span>${String(index + 1).padStart(2, "0")}</span>
      <strong>${rule}</strong>
    </div>
  `).join("");
}

function isCurrentStrategy(id) {
  return loadSelectedStrategy().id === id;
}

function strategyTypeName(name) {
  return {
    buy_hold: "买入持有",
    sma_cross: "均线交叉",
    rsi_reversion: "RSI 回归",
    hybrid_trend_rsi: "趋势过滤",
  }[name] || name || "自定义";
}

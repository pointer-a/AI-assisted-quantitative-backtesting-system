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
};

pageEl.use.addEventListener("click", () => {
  pageState.selected = saveSelectedStrategy(pageState.selected.id);
  renderList();
  renderDetail(pageState.selected);
});

renderList();
renderDetail(pageState.selected);

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
    });
  });
}

function renderDetail(strategy) {
  pageEl.title.textContent = strategy.title;
  pageEl.desc.textContent = strategy.description;
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
  }[name] || name;
}

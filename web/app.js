const state = {
  markets: [],
  prices: [],
  markers: [],
  trades: [],
  capitalEvents: [],
  metrics: [],
  hover: null,
  cursorX: null,
  hoverTimer: null,
  pendingPointer: null,
  zoomStart: null,
  zoomEnd: null,
  redrawQueued: false,
  selectedStrategy: null,
  progress: null,
  activeJobId: null,
  loadedPriceKey: "",
  timestamps: [],
  dateIndex: new Map(),
  chart: { points: [], pointByIndex: new Map(), markers: [], plot: null, range: null, yMin: null, yMax: null, xFor: null, yFor: null },
};

const el = {
  market: document.querySelector("#marketSelect"),
  years: document.querySelector("#yearList"),
  resample: document.querySelector("#resampleSelect"),
  strategySetting: document.querySelector("#strategySettingButton"),
  selectedStrategyName: document.querySelector("#selectedStrategyName"),
  selectedStrategyDesc: document.querySelector("#selectedStrategyDesc"),
  fast: document.querySelector("#fastInput"),
  slow: document.querySelector("#slowInput"),
  capital: document.querySelector("#capitalInput"),
  slippage: document.querySelector("#slippageInput"),
  commission: document.querySelector("#commissionInput"),
  load: document.querySelector("#loadButton"),
  backtest: document.querySelector("#backtestButton"),
  message: document.querySelector("#message"),
  loadProgress: document.querySelector("#loadProgress"),
  loadProgressText: document.querySelector("#loadProgressText"),
  loadProgressValue: document.querySelector("#loadProgressValue"),
  loadProgressBar: document.querySelector("#loadProgressBar"),
  title: document.querySelector("#chartTitle"),
  subtitle: document.querySelector("#chartSubtitle"),
  canvas: document.querySelector("#priceCanvas"),
  tooltip: document.querySelector("#tooltip"),
  summary: document.querySelector("#summaryGrid"),
  reportActions: document.querySelector("#reportActions"),
  reportLink: document.querySelector("#reportLink"),
};

const ctx = el.canvas.getContext("2d");
const MAX_VISIBLE_MARKER_PAIRS = 30;
const LOAD_RECORD_KEY = "ai-backtester-load-record";
const PRICE_CACHE_KEY = "ai-backtester-price-cache";

init();

async function init() {
  refreshSelectedStrategy();
  window.addEventListener("resize", drawChart);
  window.addEventListener("focus", refreshSelectedStrategy);
  el.market.addEventListener("change", () => {
    renderYears();
    saveLoadRecord(false);
  });
  el.years.addEventListener("change", () => saveLoadRecord(false));
  el.resample.addEventListener("change", () => saveLoadRecord(false));
  el.load.addEventListener("click", loadPrices);
  el.backtest.addEventListener("click", runBacktest);
  el.strategySetting.addEventListener("click", () => {
    window.location.href = "/strategy.html";
  });
  el.canvas.addEventListener("mousemove", onCanvasMove);
  el.canvas.addEventListener("wheel", onCanvasWheel, { passive: false });
  el.canvas.addEventListener("dblclick", resetZoom);
  el.canvas.addEventListener("mouseleave", () => {
    clearHoverTimer();
    state.hover = null;
    state.cursorX = null;
    state.pendingPointer = null;
    el.tooltip.style.display = "none";
    drawChart();
  });
  await loadMarkets();
  await restoreLatestJob();
}

async function loadMarkets() {
  setBusy(true);
  try {
    const savedRecord = loadSavedLoadRecord();
    const data = await apiGet("/api/markets");
    state.markets = data.markets || [];
    el.market.innerHTML = state.markets
      .map((market) => `<option value="${escapeHtml(market.id)}">${escapeHtml(market.name)}</option>`)
      .join("");
    restoreLoadControls(savedRecord);
    renderYears(savedRecord?.years);
    if (savedRecord?.resample) el.resample.value = savedRecord.resample;
    if (state.markets.length && (!savedRecord || savedRecord.loaded)) {
      await loadPrices({ restored: Boolean(savedRecord?.loaded), savedRecord });
    } else if (savedRecord) {
      showMessage("已恢复上次选择，点击加载行情继续");
    }
  } catch (error) {
    showMessage(error.message);
  } finally {
    setBusy(false);
  }
}

function renderYears(preferredYears = []) {
  const market = selectedMarket();
  const years = market ? market.years : [];
  const latest = years[years.length - 1];
  const selectedYears = new Set((preferredYears || []).map((year) => Number(year)));
  el.years.innerHTML = years
    .map((year) => `
      <label class="year-item">
        <input type="checkbox" value="${year}" ${selectedYears.size ? selectedYears.has(year) : year === latest ? "checked" : ""}>
        <span>${year}</span>
      </label>
    `)
    .join("");
}

async function loadPrices(options = {}) {
  const payload = requestPayload();
  const years = payload.years || [];
  if (!years.length) {
    showMessage("请至少选择一个年份");
    return;
  }

  // 优先从 sessionStorage 缓存恢复（页面切换返回时秒加载）
  if (!options.forceReload) {
    const cache = loadPriceCache(payload);
    if (cache) {
      applyPriceData(cache.prices, payload);
      state.markers = [];
      state.trades = [];
      state.capitalEvents = [];
      state.metrics = [];
      state.reportUrl = null;
      state.progress = null;
      resetZoom(false);
      renderSummary([]);
      renderReportLink();
      updateTitle({ market: payload.market, years: payload.years, resample: payload.resample });
      updateLoadProgress("从缓存恢复", 1, true);
      const savedRecord = options.savedRecord || loadSavedLoadRecord();
      const restoredBacktest = options.restored && restoreBacktestResult(savedRecord, payload);
      saveLoadRecord(true, restoredBacktest ? { backtest: savedRecord.backtest } : {});
      setBusy(false);
      if (restoredBacktest) {
        showMessage(`已从缓存恢复：${state.prices.length} 根K线，交易标记 ${state.markers.length} 个`);
      } else {
        showMessage(`已从缓存恢复：${state.prices.length} 根K线（秒加载）`);
      }
      drawChart();
      return;
    }
  }

  setBusy(true);
  updateLoadProgress("准备加载行情", 0, true);
  try {
    const savedRecord = options.savedRecord || loadSavedLoadRecord();
    const data = await apiPostWithProgress("/api/prices", payload, (ratio, text) => {
      updateLoadProgress(text, ratio, true);
    });
    applyPriceData(data.prices || [], payload);
    state.markers = [];
    state.trades = [];
    state.capitalEvents = [];
    state.metrics = [];
    state.reportUrl = null;
    state.progress = null;
    resetZoom(false);
    renderSummary([]);
    renderReportLink();
    updateTitle(payload);
    updateLoadProgress("行情加载完成", 1, true);
    const restoredBacktest = options.restored && restoreBacktestResult(savedRecord, payload);
    saveLoadRecord(true, restoredBacktest ? { backtest: savedRecord.backtest } : {});
    if (restoredBacktest) {
      showMessage(`已加载：${state.prices.length} 根K线，交易标记 ${state.markers.length} 个`);
    } else {
      showMessage(`已加载 ${state.prices.length} 根K线`);
    }
    drawChart();
  } catch (error) {
    updateLoadProgress("加载失败", 1, true);
    showMessage(error.message);
  } finally {
    setBusy(false);
  }
}

async function runBacktest() {
  const payload = requestPayload();
  const years = payload.years || [];
  if (!years.length) {
    showMessage("请至少选择一个年份");
    return;
  }

  // 必须先加载行情，回测不自动拉取行情数据
  if (!state.prices.length || state.loadedPriceKey !== payloadKey(payload)) {
    showMessage("请先加载行情，当前折线图数据与回测配置不一致");
    return;
  }

  setBusy(true);
  setBacktestRunning(true);
  state.progress = { phase: "回测准备", ratio: 0.02, index: 0, total: state.prices.length };
  showMessage("回测运行中，正在计算交易结果...");
  state.markers = [];
  state.trades = [];
  state.capitalEvents = [];
  state.metrics = [];
  state.reportUrl = null;
  renderSummary([]);
  renderReportLink();
  drawChart();
  try {
    payload.strategy = strategyPayload();
    payload.engine = {
      capital: Number(el.capital.value || 100000),
      commission: Number(el.commission.value || 0),
      slippage_bps: Number(el.slippage.value || 0),
    };
    const jobResponse = await apiPost("/api/backtest-jobs", payload);
    const job = jobResponse.job;
    state.activeJobId = job.id;
    showMessage(`回测任务已提交：${job.id}，后台运行中...`);
    const data = await apiStreamBacktestJob(job.id);
    applyBacktestResult(data, payload);
  } catch (error) {
    state.progress = null;
    showMessage(error.message);
  } finally {
    setBacktestRunning(false);
    setBusy(false);
  }
}

function requestPayload() {
  const years = [...el.years.querySelectorAll("input:checked")]
    .map((input) => Number(input.value))
    .filter((year) => Number.isInteger(year))
    .sort((left, right) => left - right);
  return {
    market: el.market.value,
    years: [...new Set(years)],
    resample: el.resample.value,
  };
}

function payloadKey(payload) {
  const years = [...(payload.years || [])].map(Number).sort((left, right) => left - right);
  return `${payload.market}|${payload.resample}|${years.join(",")}`;
}

function applyPriceData(prices, payload) {
  state.prices = prices;
  state.loadedPriceKey = payloadKey(payload);
  indexPrices();
  savePriceCache(payload, prices);
}

function savePriceCache(payload, prices) {
  try {
    const cache = {
      key: payloadKey(payload),
      prices: prices,
      market: payload.market,
      years: payload.years,
      resample: payload.resample,
      savedAt: Date.now(),
    };
    sessionStorage.setItem(PRICE_CACHE_KEY, JSON.stringify(cache));
  } catch {
    // sessionStorage 满时静默失败，不影响主流程
  }
}

function loadPriceCache(payload) {
  try {
    const raw = sessionStorage.getItem(PRICE_CACHE_KEY);
    if (!raw) return null;
    const cache = JSON.parse(raw);
    if (!cache || !cache.prices || !Array.isArray(cache.prices) || !cache.prices.length) return null;
    if (cache.key !== payloadKey(payload)) return null;
    return cache;
  } catch {
    return null;
  }
}

function loadSavedLoadRecord() {
  try {
    const record = JSON.parse(localStorage.getItem(LOAD_RECORD_KEY) || "null");
    if (!record || !record.market || !Array.isArray(record.years)) return null;
    return record;
  } catch {
    return null;
  }
}

function saveLoadRecord(loaded, extra = {}) {
  const payload = requestPayload();
  const record = {
    market: payload.market,
    years: payload.years,
    resample: payload.resample,
    loaded: Boolean(loaded),
    loadedAt: loaded ? new Date().toISOString() : null,
    ...extra,
  };
  try {
    localStorage.setItem(LOAD_RECORD_KEY, JSON.stringify(record));
  } catch {
    showMessage("浏览器本地存储空间不足，无法保存加载记录");
  }
}

function restoreLoadControls(record) {
  if (!record) return;
  if (state.markets.some((market) => market.id === record.market)) {
    el.market.value = record.market;
  }
}

function restoreBacktestResult(record, payload) {
  if (!record?.backtest || payloadKey(record) !== payloadKey(payload)) return false;
  state.markers = record.backtest.markers || [];
  state.trades = record.backtest.trades || [];
  state.capitalEvents = record.backtest.capitalEvents || record.backtest.capital_events || [];
  state.metrics = record.backtest.metrics || [];
  state.reportUrl = record.backtest.reportUrl || null;
  indexAnnotations();
  renderSummary(state.metrics);
  renderReportLink();
  return true;
}

async function restoreLatestJob() {
  try {
    const data = await apiGet("/api/backtest-jobs/latest");
    const job = data.job;
    if (!job || !job.payload) return;

    if (job.status === "queued" || job.status === "running") {
      state.activeJobId = job.id;
      restoreControlsFromPayload(job.payload);
      await ensurePricesForPayload(job.payload);
      setBusy(true);
      setBacktestRunning(true);
      showMessage(`已恢复后台回测任务：${job.id}`);
      const result = await apiStreamBacktestJob(job.id);
      applyBacktestResult(result, job.payload);
      setBacktestRunning(false);
      setBusy(false);
      return;
    }

    if (job.status === "completed" && job.result) {
      restoreControlsFromPayload(job.payload);
      await ensurePricesForPayload(job.payload);
      applyBacktestResult(job.result, job.payload);
      showMessage(`已恢复最近完成的回测任务：${job.id}`);
    }
  } catch (error) {
    state.activeJobId = null;
    state.progress = null;
    setBacktestRunning(false);
    setBusy(false);
    showMessage(error.message);
  }
}

async function ensurePricesForPayload(payload) {
  if (state.loadedPriceKey === payloadKey(payload) && state.prices.length) return;

  // 优先从 sessionStorage 缓存恢复
  const cache = loadPriceCache(payload);
  if (cache) {
    applyPriceData(cache.prices, payload);
    updateTitle(payload);
    drawChart();
    return;
  }

  updateLoadProgress("恢复行情数据", 0, true);
  const priceData = await apiPostWithProgress("/api/prices", payload, (ratio, text) => {
    updateLoadProgress(text, ratio, true);
  });
  applyPriceData(priceData.prices || [], payload);
  updateTitle(payload);
  updateLoadProgress("行情加载完成", 1, true);
  drawChart();
}

function restoreControlsFromPayload(payload) {
  if (!payload) return;
  if (state.markets.some((market) => market.id === payload.market)) {
    el.market.value = payload.market;
    renderYears(payload.years || []);
  }
  if (payload.resample) el.resample.value = payload.resample;
}

function applyBacktestResult(data, payload) {
  if (data.prices && data.prices.length) {
    applyPriceData(data.prices || [], payload);
  }
  state.markers = data.markers || [];
  state.trades = data.trades || [];
  state.capitalEvents = data.capital_events || [];
  indexAnnotations();
  state.metrics = data.metrics || [];
  state.progress = null;
  state.activeJobId = null;
  state.reportUrl = data.report_url || null;
  resetZoom(false);
  renderSummary(state.metrics);
  renderReportLink();
  updateTitle({ ...payload, bar_count: data.bar_count, start_date: data.start_date, end_date: data.end_date });
  saveLoadRecord(true, {
    backtest: {
      markers: state.markers,
      trades: state.trades,
      capitalEvents: state.capitalEvents,
      metrics: state.metrics,
      reportUrl: state.reportUrl,
      completedAt: new Date().toISOString(),
    },
  });
  showMessage(`回测完成，交易标记 ${state.markers.length} 个，交易区间 ${state.trades.length} 段`);
  drawChart();
}

function renderReportLink() {
  if (!el.reportActions || !el.reportLink) return;
  if (state.reportUrl) {
    el.reportLink.href = state.reportUrl;
    el.reportActions.hidden = false;
  } else {
    el.reportActions.hidden = true;
  }
}

function strategyPayload() {
  const strategy = state.selectedStrategy || defaultStrategy();
  return {
    name: strategy.strategy,
    fast: Number(el.fast.value || 10),
    slow: Number(el.slow.value || 30),
    period: Number(strategy.params.period || 14),
    buy_below: Number(strategy.params.buy_below || 35),
    sell_above: Number(strategy.params.sell_above || 65),
    rsi_period: Number(strategy.params.rsi_period || 14),
    max_rsi: Number(strategy.params.max_rsi || 72),
  };
}

function refreshSelectedStrategy() {
  const strategy = loadSelectedStrategy();
  state.selectedStrategy = strategy;
  if (el.selectedStrategyName) el.selectedStrategyName.textContent = strategy.title;
  if (el.selectedStrategyDesc) {
    el.selectedStrategyDesc.textContent = `收益 ${strategy.returnText} · 胜率 ${strategy.winRateText}`;
  }
  if (el.fast && strategy.params.fast) el.fast.value = strategy.params.fast;
  if (el.slow && strategy.params.slow) el.slow.value = strategy.params.slow;
}

function selectedMarket() {
  return state.markets.find((market) => market.id === el.market.value);
}

function updateTitle(payload) {
  el.title.textContent = `${payload.market.toUpperCase()} 收盘价`;
  const firstDate = payload.start_date || state.prices[0]?.date;
  const lastDate = payload.end_date || state.prices[state.prices.length - 1]?.date;
  const rangeText = firstDate && lastDate ? ` · ${formatAxisTime(firstDate)} 至 ${formatAxisTime(lastDate)}` : "";
  const countText = payload.bar_count || state.prices.length;
  el.subtitle.textContent = `${payload.years.join(", ")} · ${cycleName(payload.resample)} · ${countText} 根K线${rangeText}`;
}

function drawChart() {
  const rect = el.canvas.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  el.canvas.width = Math.max(600, Math.floor(rect.width * ratio));
  el.canvas.height = Math.max(360, Math.floor(rect.height * ratio));
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);

  const width = rect.width;
  const height = rect.height;
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#171c24";
  ctx.fillRect(0, 0, width, height);

  if (!state.prices.length) {
    ctx.fillStyle = "#8e9aab";
    ctx.font = "15px Arial";
    ctx.fillText("暂无行情数据", 28, 44);
    return;
  }

  const range = visibleRange();
  const pad = { left: 58, right: 28, top: 26, bottom: 54 };
  const plot = {
    x: pad.left,
    y: pad.top,
    w: width - pad.left - pad.right,
    h: height - pad.top - pad.bottom,
  };
  const visibleMarkerItems = visibleMarkersForRange(range);
  const shouldDrawMarkers = Math.ceil(visibleMarkerItems.length / 2) <= MAX_VISIBLE_MARKER_PAIRS;
  const bounds = visibleBounds(range, shouldDrawMarkers ? visibleMarkerItems : []);
  const span = bounds.max - bounds.min || 1;
  const yMin = bounds.min - span * 0.08;
  const yMax = bounds.max + span * 0.08;

  const xFor = (index) => plot.x + (range.end === range.start ? 0 : ((index - range.start) / (range.end - range.start)) * plot.w);
  const yFor = (price) => plot.y + (1 - (price - yMin) / (yMax - yMin)) * plot.h;

  drawGrid(plot, yMin, yMax, range);
  const points = buildSampledIndices(range.start, range.end, Math.max(240, Math.floor(plot.w * 1.5)))
    .map((index) => pointForIndex(index, xFor, yFor))
    .filter(Boolean);
  state.chart.points = points;
  state.chart.pointByIndex = new Map(points.map((point) => [point.index, point]));
  state.chart.plot = plot;
  state.chart.range = range;
  state.chart.yMin = yMin;
  state.chart.yMax = yMax;
  state.chart.xFor = xFor;
  state.chart.yFor = yFor;

  ctx.beginPath();
  points.forEach((point, index) => {
    if (index === 0) ctx.moveTo(point.x, point.y);
    else ctx.lineTo(point.x, point.y);
  });
  ctx.strokeStyle = "#3ab7bf";
  ctx.lineWidth = 2;
  ctx.stroke();

  drawTradeSegments(range, xFor, yFor);
  drawCapitalEvents(range, xFor);

  state.chart.markers = shouldDrawMarkers ? visibleMarkerItems.map(({ marker, index }) => {
    return {
      x: xFor(index),
      y: yFor(marker.price),
      data: marker,
      priceIndex: index,
    };
  }) : [];

  for (const marker of state.chart.markers) {
    drawMarker(marker);
  }

  if (state.cursorX !== null) drawCursorLine(state.cursorX);
  if (state.progress) drawProgressOverlay(state.progress);
  if (state.hover) drawHover(state.hover);
}

function drawTradeSegments(range, xFor, yFor) {
  if (!state.trades.length) return;

  for (const trade of state.trades) {
    const entryIndex = Number.isInteger(trade.entryIndex) ? trade.entryIndex : nearestIndexByDate(trade.entry_date);
    const exitIndex = Number.isInteger(trade.exitIndex) ? trade.exitIndex : nearestIndexByDate(trade.exit_date);
    const start = Math.max(entryIndex, range.start);
    const end = Math.min(exitIndex, range.end);
    if (end <= start) continue;
    const indices = buildSampledIndices(start, end, Math.max(80, Math.floor(state.chart.plot.w * 0.9)));
    if (indices.length < 2) continue;

    ctx.save();
    ctx.beginPath();
    indices.forEach((index, position) => {
      const point = pointForIndex(index, xFor, yFor);
      if (!point) return;
      if (position === 0) ctx.moveTo(point.x, point.y);
      else ctx.lineTo(point.x, point.y);
    });
    ctx.strokeStyle = trade.pnl >= 0 ? "#22c55e" : "#ef4444";
    ctx.lineWidth = 4;
    ctx.shadowColor = trade.pnl >= 0 ? "rgba(34, 197, 94, .35)" : "rgba(239, 68, 68, .35)";
    ctx.shadowBlur = 8;
    ctx.stroke();
    ctx.restore();
  }
}

function drawCapitalEvents(range, xFor) {
  if (!state.capitalEvents.length) return;

  for (const event of state.capitalEvents) {
    const index = Number.isInteger(event.priceIndex) ? event.priceIndex : nearestIndexByDate(event.date);
    if (index < range.start || index > range.end) continue;
    const offset = event.type === "zero" ? -2 : 2;
    const x = xFor(index) + offset;

    ctx.save();
    ctx.strokeStyle = event.color || (event.type === "zero" ? "#ef4444" : "#f5b700");
    ctx.lineWidth = 2;
    ctx.setLineDash(event.type === "zero" ? [] : [5, 4]);
    ctx.beginPath();
    ctx.moveTo(x, state.chart.plot.y);
    ctx.lineTo(x, state.chart.plot.y + state.chart.plot.h);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = ctx.strokeStyle;
    ctx.font = "12px Arial";
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    ctx.fillText(event.type === "zero" ? "归零" : "补充", x, state.chart.plot.y + 8);
    ctx.restore();
  }
}

function drawGrid(plot, yMin, yMax, range) {
  ctx.strokeStyle = "rgba(148, 163, 184, .13)";
  ctx.lineWidth = 1;
  ctx.fillStyle = "#8e9aab";
  ctx.font = "12px Arial";
  ctx.textAlign = "right";
  ctx.textBaseline = "middle";

  for (let i = 0; i <= 5; i++) {
    const y = plot.y + (plot.h / 5) * i;
    ctx.beginPath();
    ctx.moveTo(plot.x, y);
    ctx.lineTo(plot.x + plot.w, y);
    ctx.stroke();
    const value = yMax - ((yMax - yMin) / 5) * i;
    ctx.fillText(formatNumber(value), plot.x - 8, y);
  }

  for (let i = 0; i <= 6; i++) {
    const x = plot.x + (plot.w / 6) * i;
    ctx.beginPath();
    ctx.moveTo(x, plot.y);
    ctx.lineTo(x, plot.y + plot.h);
    ctx.stroke();

    const priceIndex = Math.round(range.start + (range.end - range.start) * (i / 6));
    const label = formatAxisTime(state.prices[priceIndex]?.date);
    ctx.save();
    ctx.fillStyle = "#8e9aab";
    ctx.font = "12px Arial";
    ctx.textAlign = i === 0 ? "left" : i === 6 ? "right" : "center";
    ctx.textBaseline = "top";
    ctx.fillText(label, x, plot.y + plot.h + 12);
    ctx.restore();
  }

  ctx.save();
  ctx.fillStyle = "#64748b";
  ctx.font = "12px Arial";
  ctx.textAlign = "right";
  ctx.textBaseline = "bottom";
  ctx.fillText("时间", plot.x + plot.w, plot.y + plot.h + 44);
  ctx.restore();
}

function drawMarker(marker) {
  const item = marker.data;
  ctx.save();
  ctx.fillStyle = item.color;
  ctx.strokeStyle = "#0f172a";
  ctx.lineWidth = 2;
  ctx.beginPath();
  if (item.type === "entry") {
    ctx.moveTo(marker.x, marker.y - 8);
    ctx.lineTo(marker.x + 7, marker.y + 6);
    ctx.lineTo(marker.x - 7, marker.y + 6);
  } else {
    ctx.moveTo(marker.x, marker.y + 8);
    ctx.lineTo(marker.x + 7, marker.y - 6);
    ctx.lineTo(marker.x - 7, marker.y - 6);
  }
  ctx.closePath();
  ctx.fill();
  ctx.stroke();
  ctx.restore();
}

function drawHover(hover) {
  const plot = state.chart.plot;
  drawCursorLine(hover.x);
  ctx.beginPath();
  ctx.arc(hover.x, hover.y, 4, 0, Math.PI * 2);
  ctx.fillStyle = "#e2e8f0";
  ctx.fill();
}

function drawProgressOverlay(progress) {
  const plot = state.chart.plot;
  const range = state.chart.range;
  if (!plot || !range) return;
  const total = Math.max(1, state.prices.length - 1);
  const rawIndex = progress.index ?? Math.round((progress.ratio || 0) * total);
  const index = Math.max(0, Math.min(total, rawIndex));
  const x = plot.x + ((index - range.start) / Math.max(1, range.end - range.start)) * plot.w;
  const clampedX = Math.max(plot.x, Math.min(plot.x + plot.w, x));

  ctx.save();
  ctx.fillStyle = "rgba(59, 130, 246, .12)";
  ctx.fillRect(plot.x, plot.y, Math.max(0, clampedX - plot.x), plot.h);
  ctx.strokeStyle = "#60a5fa";
  ctx.setLineDash([6, 4]);
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.moveTo(clampedX, plot.y);
  ctx.lineTo(clampedX, plot.y + plot.h);
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.fillStyle = "#bfdbfe";
  ctx.font = "12px Arial";
  ctx.textAlign = "left";
  ctx.textBaseline = "top";
  const label = `${progress.phase || "回测中"} ${Math.round((progress.ratio || 0) * 100)}%`;
  ctx.fillText(label, plot.x + 10, plot.y + 10);
  ctx.restore();
}

function drawCursorLine(x) {
  const plot = state.chart.plot;
  if (!plot) return;
  const clampedX = Math.max(plot.x, Math.min(plot.x + plot.w, x));
  ctx.strokeStyle = "rgba(226, 232, 240, .35)";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(clampedX, plot.y);
  ctx.lineTo(clampedX, plot.y + plot.h);
  ctx.stroke();
}

function onCanvasMove(event) {
  if (!state.chart.points.length) return;
  const rect = el.canvas.getBoundingClientRect();
  state.hover = null;
  state.cursorX = event.clientX - rect.left;
  state.pendingPointer = {
    x: state.cursorX,
    y: event.clientY - rect.top,
    clientX: event.clientX,
    clientY: event.clientY,
  };
  el.tooltip.style.display = "none";
  scheduleChartDraw();
  clearHoverTimer();
  state.hoverTimer = window.setTimeout(() => showHoverAtPointer(), 300);
}

function showHoverAtPointer() {
  const pointer = state.pendingPointer;
  if (!pointer || !state.chart.points.length) return;
  const x = pointer.x;
  const y = pointer.y;

  let nearestMarker = null;
  let markerDistance = Infinity;
  for (const marker of state.chart.markers) {
    const distance = Math.hypot(marker.x - x, marker.y - y);
    if (distance < markerDistance) {
      markerDistance = distance;
      nearestMarker = marker;
    }
  }

  if (nearestMarker && markerDistance <= 14) {
    state.hover = { x: nearestMarker.x, y: nearestMarker.y };
    showTooltip(pointer, markerTooltip(nearestMarker.data));
    drawChart();
    return;
  }

  const plot = state.chart.plot;
  const range = state.chart.range;
  const rawIndex = Math.round(range.start + ((x - plot.x) / plot.w) * (range.end - range.start));
  const index = Math.max(range.start, Math.min(range.end, rawIndex));
  const point = pointForIndex(index, state.chart.xFor, state.chart.yFor);
  if (!point) return;
  state.hover = point;
  showTooltip(pointer, priceTooltip(point.data));
  drawChart();
}

function showTooltip(pointer, html) {
  const rect = el.canvas.parentElement.getBoundingClientRect();
  el.tooltip.innerHTML = html;
  el.tooltip.style.display = "block";
  const left = Math.min(rect.width - 280, Math.max(10, pointer.clientX - rect.left + 14));
  const top = Math.min(rect.height - 150, Math.max(10, pointer.clientY - rect.top + 14));
  el.tooltip.style.left = `${left}px`;
  el.tooltip.style.top = `${top}px`;
}

function onCanvasWheel(event) {
  if (!state.prices.length || !state.chart.plot) return;
  event.preventDefault();
  clearHoverTimer();
  state.hover = null;
  el.tooltip.style.display = "none";

  const plot = state.chart.plot;
  const range = visibleRange();
  const rect = el.canvas.getBoundingClientRect();
  const x = event.clientX - rect.left;
  const ratio = Math.max(0, Math.min(1, (x - plot.x) / plot.w));
  const total = state.prices.length;
  const currentSize = range.end - range.start + 1;
  const zoomIn = event.deltaY < 0;
  const factor = zoomIn ? 0.72 : 1.38;
  const minSize = Math.min(total, 20);
  const nextSize = Math.max(minSize, Math.min(total, Math.round(currentSize * factor)));

  const anchor = range.start + ratio * (currentSize - 1);
  let start = Math.round(anchor - ratio * (nextSize - 1));
  let end = start + nextSize - 1;
  if (start < 0) {
    end -= start;
    start = 0;
  }
  if (end >= total) {
    start -= end - total + 1;
    end = total - 1;
  }

  state.zoomStart = Math.max(0, start);
  state.zoomEnd = Math.min(total - 1, end);
  state.cursorX = null;
  drawChart();
}

function resetZoom(redraw = true) {
  state.zoomStart = null;
  state.zoomEnd = null;
  state.hover = null;
  state.cursorX = null;
  state.pendingPointer = null;
  clearHoverTimer();
  el.tooltip.style.display = "none";
  if (redraw) drawChart();
}

function visibleRange() {
  const total = state.prices.length;
  let start = state.zoomStart ?? 0;
  let end = state.zoomEnd ?? total - 1;
  start = Math.max(0, Math.min(total - 1, start));
  end = Math.max(start, Math.min(total - 1, end));
  if (end - start < 2 && total > 2) {
    end = Math.min(total - 1, start + 2);
  }
  return { start, end };
}

function clearHoverTimer() {
  if (state.hoverTimer) {
    window.clearTimeout(state.hoverTimer);
    state.hoverTimer = null;
  }
}

function scheduleChartDraw() {
  if (state.redrawQueued) return;
  state.redrawQueued = true;
  window.requestAnimationFrame(() => {
    state.redrawQueued = false;
    drawChart();
  });
}

function priceTooltip(item) {
  return `
    <strong>${item.date}</strong><br>
    开盘：${formatNumber(item.open)}<br>
    最高：${formatNumber(item.high)}<br>
    最低：${formatNumber(item.low)}<br>
    收盘：${formatNumber(item.close)}<br>
    成交量：${formatNumber(item.volume)}
  `;
}

function markerTooltip(item) {
  return `
    <strong>${item.label} · ${item.status}</strong><br>
    时间：${item.date}<br>
    价格：${formatNumber(item.price)}<br>
    盈亏：${formatNumber(item.pnl)}<br>
    收益率：${formatPercent(item.return_pct)}
  `;
}

function nearestIndexByDate(date) {
  if (!state.prices.length) return 0;
  const key = typeof date === "string" ? date : String(date || "");
  if (state.dateIndex.has(key)) {
    return state.dateIndex.get(key);
  }
  const target = Date.parse(key);
  if (!Number.isFinite(target)) return 0;
  const timestamps = state.timestamps;
  let low = 0;
  let high = timestamps.length - 1;
  while (low <= high) {
    const mid = (low + high) >> 1;
    const value = timestamps[mid];
    if (value === target) return mid;
    if (value < target) low = mid + 1;
    else high = mid - 1;
  }
  if (low <= 0) return 0;
  if (low >= timestamps.length) return timestamps.length - 1;
  const left = low - 1;
  const right = low;
  return Math.abs(timestamps[right] - target) < Math.abs(timestamps[left] - target) ? right : left;
}

function indexPrices() {
  const timestamps = new Array(state.prices.length);
  const dateIndex = new Map();
  state.prices.forEach((item, index) => {
    const time = Date.parse(item.date);
    timestamps[index] = Number.isFinite(time) ? time : 0;
    if (!dateIndex.has(item.date)) {
      dateIndex.set(item.date, index);
    }
  });
  state.timestamps = timestamps;
  state.dateIndex = dateIndex;
}

function indexAnnotations() {
  state.markers = state.markers.map((marker) => ({
    ...marker,
    priceIndex: nearestIndexByDate(marker.date),
  }));
  state.trades = state.trades.map((trade) => ({
    ...trade,
    entryIndex: nearestIndexByDate(trade.entry_date),
    exitIndex: nearestIndexByDate(trade.exit_date),
  }));
  state.capitalEvents = state.capitalEvents.map((event) => ({
    ...event,
    priceIndex: nearestIndexByDate(event.date),
  }));
}

function visibleMarkersForRange(range) {
  return state.markers
    .map((marker) => ({
      marker,
      index: Number.isInteger(marker.priceIndex) ? marker.priceIndex : nearestIndexByDate(marker.date),
    }))
    .filter((item) => item.index >= range.start && item.index <= range.end);
}

function visibleBounds(range, visibleMarkerItems = []) {
  let min = Infinity;
  let max = -Infinity;
  for (let index = range.start; index <= range.end; index++) {
    const value = Number(state.prices[index]?.close);
    if (!Number.isFinite(value)) continue;
    if (value < min) min = value;
    if (value > max) max = value;
  }
  for (const { marker } of visibleMarkerItems) {
    const value = Number(marker.price);
    if (!Number.isFinite(value)) continue;
    if (value < min) min = value;
    if (value > max) max = value;
  }
  if (!Number.isFinite(min) || !Number.isFinite(max)) {
    const fallback = Number(state.prices[range.start]?.close || 0);
    return { min: fallback, max: fallback || 1 };
  }
  return { min, max };
}

function buildSampledIndices(start, end, maxPoints) {
  const total = end - start + 1;
  if (total <= 0) return [];
  if (total <= maxPoints) {
    return Array.from({ length: total }, (_, index) => start + index);
  }

  const bucketCount = Math.max(1, Math.floor(maxPoints / 2));
  const bucketSize = Math.max(1, Math.ceil(total / bucketCount));
  const indices = [];

  for (let bucketStart = start; bucketStart <= end; bucketStart += bucketSize) {
    const bucketEnd = Math.min(end, bucketStart + bucketSize - 1);
    let minIndex = bucketStart;
    let maxIndex = bucketStart;
    let minValue = Number(state.prices[bucketStart]?.close || 0);
    let maxValue = minValue;

    for (let index = bucketStart + 1; index <= bucketEnd; index++) {
      const value = Number(state.prices[index]?.close || 0);
      if (value < minValue) {
        minValue = value;
        minIndex = index;
      }
      if (value > maxValue) {
        maxValue = value;
        maxIndex = index;
      }
    }

    indices.push(minIndex);
    if (maxIndex !== minIndex) indices.push(maxIndex);
  }

  if (indices[0] !== start) indices.unshift(start);
  if (indices[indices.length - 1] !== end) indices.push(end);
  indices.sort((left, right) => left - right);
  return indices.filter((value, index) => index === 0 || value !== indices[index - 1]);
}

function pointForIndex(index, xFor, yFor) {
  const item = state.prices[index];
  if (!item || !xFor || !yFor) return null;
  return {
    x: xFor(index),
    y: yFor(Number(item.close)),
    data: item,
    index,
  };
}

function renderSummary(metrics) {
  const keys = ["final_equity", "total_return", "max_drawdown", "sharpe", "trade_count", "win_rate", "profit_factor", "buy_hold_return"];
  const byKey = Object.fromEntries(metrics.map((item) => [item.key, item]));
  el.summary.innerHTML = keys
    .filter((key) => byKey[key])
    .map((key) => `
      <article class="metric-card">
        <span>${byKey[key].label}</span>
        <strong>${formatMetric(key, byKey[key].value)}</strong>
      </article>
    `)
    .join("");
}

async function apiGet(url) {
  const response = await fetch(url);
  const data = await response.json();
  if (!response.ok) throw new Error(errorMessage(data));
  return data;
}

async function apiPost(url, payload) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(errorMessage(data));
  return data;
}

async function apiPostWithProgress(url, payload, onProgress) {
  onProgress?.(0.03, "发送加载请求");
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (!response.ok || !response.body) {
    const data = await response.json().catch(() => ({}));
    throw new Error(errorMessage(data));
  }

  const total = Number(response.headers.get("Content-Length") || 0);
  const reader = response.body.getReader();
  const chunks = [];
  let loaded = 0;
  onProgress?.(0.08, "接收行情数据");

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    if (!value) continue;
    chunks.push(value);
    loaded += value.length;
    const ratio = total > 0 ? Math.min(0.96, loaded / total) : 0.35;
    onProgress?.(ratio, total > 0 ? "接收行情数据" : "行情加载中");
  }

  onProgress?.(0.98, "解析行情数据");
  const size = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.length;
  }
  return JSON.parse(new TextDecoder("utf-8").decode(bytes));
}

async function apiStreamBacktest(url, payload) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok || !response.body) {
    const data = await response.json().catch(() => ({}));
    throw new Error(errorMessage(data));
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  let result = null;

  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";

    for (const line of lines) {
      if (!line.trim()) continue;
      const event = JSON.parse(line);
      if (event.type === "progress") {
        state.progress = event;
        showMessage(`回测进度：${Math.round((event.ratio || 0) * 100)}% · ${event.phase || "回测中"}`);
      } else if (event.type === "result") {
        result = event.payload;
      } else if (event.type === "error") {
        throw new Error(errorMessage(event.payload || {}));
      }
    }

    if (done) break;
  }

  if (!result) throw new Error("回测结果为空");
  return result;
}

async function apiStreamBacktestJob(jobId) {
  const response = await fetch(`/api/backtest-jobs/${encodeURIComponent(jobId)}/stream`);
  if (!response.ok || !response.body) {
    const data = await response.json().catch(() => ({}));
    throw new Error(errorMessage(data));
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  let result = null;

  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";

    for (const line of lines) {
      if (!line.trim()) continue;
      const event = JSON.parse(line);
      if (event.type === "progress") {
        state.progress = event;
        showMessage(`回测进度：${Math.round((event.ratio || 0) * 100)}% · ${event.phase || "回测中"}`);
      } else if (event.type === "result") {
        result = event.payload;
      } else if (event.type === "error") {
        throw new Error(errorMessage(event.payload || {}));
      }
    }

    if (done) break;
  }

  if (!result) throw new Error("回测结果为空");
  return result;
}

function errorMessage(data) {
  if (data.max_contiguous_years && data.max_contiguous_years.length) {
    return `${data.error}。缺失年份：${data.missing_years.join(", ")}。可用最大连续年份：${data.max_contiguous_years.join(", ")}`;
  }
  return data.error || "请求失败";
}

function setBusy(busy) {
  el.load.disabled = busy;
  el.backtest.disabled = busy || !state.prices.length;
}

function setBacktestRunning(running) {
  el.backtest.textContent = running ? "回测中..." : "开始回测";
}

function showMessage(text) {
  el.message.textContent = text || "";
}

function updateLoadProgress(text, ratio, visible) {
  if (!el.loadProgress) return;
  const percent = Math.max(0, Math.min(100, Math.round((ratio || 0) * 100)));
  el.loadProgress.hidden = !visible;
  el.loadProgressText.textContent = text || "加载行情";
  el.loadProgressValue.textContent = `${percent}%`;
  el.loadProgressBar.style.width = `${percent}%`;
}

function formatMetric(key, value) {
  if (["total_return", "buy_hold_return", "cagr", "max_drawdown", "win_rate"].includes(key)) {
    return formatPercent(value);
  }
  if (key === "trade_count") return String(Math.round(value));
  return formatNumber(value);
}

function formatPercent(value) {
  return `${(Number(value) * 100).toFixed(2)}%`;
}

function formatNumber(value) {
  return Number(value).toLocaleString("zh-CN", { maximumFractionDigits: 2 });
}

function formatAxisTime(value) {
  if (!value) return "";
  const text = String(value);
  const datePart = text.slice(0, 10);
  const timePart = text.slice(11, 16);
  if (!timePart || timePart === "00:00") return datePart;
  return `${datePart.slice(5)} ${timePart}`;
}

function cycleName(value) {
  return { daily: "日线", hourly: "小时线", none: "原始K线" }[value] || value;
}

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[char]));
}

/* ==========================================================
   dashboard.js
   ----------------------------------------------------------
   Everything the market page DOES lives here.

   TWO CHARTS: the same company is drawn twice, once for NSE
   and once for BSE, because the same share trades on both
   Indian exchanges at slightly different prices.

   REFRESH: both charts reload every 2 minutes. There is also
   a "Refresh now" button if you do not want to wait.

   TIME: the backend already shifts every candle by +5:30, so
   the time axis you see is real IST clock time.
   ========================================================== */

const REFRESH_MS = 2 * 60 * 1000;    // charts reload every 2 minutes
const PORTFOLIO_MS = 30 * 1000;      // portfolio reprices every 30 seconds

const charts = { NSE: null, BSE: null };   // one entry per exchange
let activeBase = window.BASE_TICKERS[0];   // e.g. "RELIANCE"
let currentInterval = '15m';               // candle size: "2m" or "15m"
let lastRecoId = null;
let refreshTimer = null;
let portfolioTimer = null;

/* ---------------------------------------------- IST clock

   Built from the computer's own clock, converted to Indian time, so the
   header clock always matches the time axis on the charts.               */

function istNow() {
  const now = new Date();
  return new Date(now.getTime() + 5.5 * 60 * 60 * 1000);
}

function tickClock() {
  const el = document.getElementById('istClock');
  if (el) el.textContent = istNow().toISOString().substr(11, 8);
}

/* Turn "RELIANCE" + "NSE" into "RELIANCE.NS". */
function symbolFor(base, exchange) {
  return base + window.EXCHANGES[exchange].suffix;
}

/* ---------------------------------------------- chart setup */

function buildChart(exchange, containerId) {
  const box = document.getElementById(containerId);
  if (!box || typeof LightweightCharts === 'undefined') {
    document.getElementById('note' + exchange).textContent =
      'Chart library could not load. Check your internet connection.';
    return null;
  }

  const chart = LightweightCharts.createChart(box, {
    layout: { background: { color: '#11131f' }, textColor: '#9aa0bd' },
    grid: {
      vertLines: { color: 'rgba(38,42,64,0.5)' },
      horzLines: { color: 'rgba(38,42,64,0.5)' }
    },
    rightPriceScale: { borderColor: '#262a40' },
    timeScale: {
      borderColor: '#262a40',
      timeVisible: true,      // show hours and minutes, not just dates
      secondsVisible: false
    },
    crosshair: { mode: 0 },
    width: box.clientWidth,
    height: box.clientHeight || 320
  });

  const options = {
    upColor: '#16c784', downColor: '#e62429',
    borderUpColor: '#16c784', borderDownColor: '#e62429',
    wickUpColor: '#16c784', wickDownColor: '#e62429'
  };

  // Version guard: v4 uses addCandlestickSeries, v5 uses addSeries.
  const series = chart.addCandlestickSeries
    ? chart.addCandlestickSeries(options)
    : chart.addSeries(LightweightCharts.CandlestickSeries, options);

  window.addEventListener('resize', () => {
    chart.applyOptions({ width: box.clientWidth });
  });

  return { chart: chart, series: series };
}

/* ---------------------------------------------- loading data */

async function loadChart(exchange) {
  const ticker = symbolFor(activeBase, exchange);
  const breakPrices = document.getElementById('breakPrices').checked ? '1' : '0';

  const priceEl = document.getElementById('price' + exchange);
  const badgeEl = document.getElementById('badge' + exchange);
  const noteEl = document.getElementById('note' + exchange);

  try {
    const response = await fetch(
      `/api/candles/${ticker}?break_prices=${breakPrices}&interval=${currentInterval}`);
    const data = await response.json();

    if (!data.ok) {
      noteEl.textContent = 'Error: ' + data.error;
      badgeEl.className = 'badge badge-sim';
      badgeEl.textContent = 'ERROR';
      return null;
    }

    if (charts[exchange]) {
      charts[exchange].series.setData(data.candles);
      charts[exchange].chart.timeScale().fitContent();
    }

    const last = data.candles[data.candles.length - 1];
    priceEl.textContent = 'Rs ' + last.close.toLocaleString('en-IN');

    if (data.quality === 'DEGRADED') {
      badgeEl.className = 'badge badge-sim';
      badgeEl.textContent = 'SIMULATED';
      // Say WHY, rather than leaving the user to guess why this one company
      // looks different from the others.
      noteEl.textContent = (data.reason || 'Live feed unavailable.')
        + ' Showing a deterministic simulated series; price-based conclusions '
        + 'carry reduced confidence.';
    } else {
      // The newest BSE candle is topped up from BSE's own live feed, because
      // the history provider publishes BSE bars about 15 minutes late.
      const topUp = data.live_patched ? ' · newest candle from BSE live feed' : '';

      if (data.market_open) {
        badgeEl.className = 'badge badge-live';
        badgeEl.textContent = 'LIVE';
        noteEl.textContent = `Real ${exchange} data · ${data.candles.length} × `
          + `${data.interval_label} · last ${formatIST(last.time)} IST${topUp}`;
      } else {
        badgeEl.className = 'badge badge-sim';
        badgeEl.textContent = 'CLOSED';
        noteEl.textContent = `Real ${exchange} data · ${data.interval_label} · `
          + `market closed · last candle ${formatIST(last.time)} IST${topUp}`;
      }
    }

    return last.close;

  } catch (error) {
    noteEl.textContent = 'Could not load chart: ' + error;
    return null;
  }
}

/* The backend already added +5:30, so we read the timestamp back as UTC
   to display the IST clock time it represents. */
function formatIST(unixSeconds) {
  const d = new Date(unixSeconds * 1000);
  const pad = (n) => String(n).padStart(2, '0');
  return `${pad(d.getUTCDate())}/${pad(d.getUTCMonth() + 1)} `
       + `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
}

/* Reload BOTH charts and the price strip. Called every 2 minutes. */
async function refreshAll() {
  document.getElementById('stockName').textContent =
    window.COMPANIES[activeBase] || activeBase;

  const [nsePrice, bsePrice] = await Promise.all([
    loadChart('NSE'),
    loadChart('BSE')
  ]);

  // The portfolio is repriced in the SAME cycle as the charts, so the value
  // you see is always the value behind the candle you are looking at.
  await Promise.all([refreshPrices(), refreshPortfolio()]);
  showSpread(nsePrice, bsePrice);

  document.getElementById('lastUpdated').textContent =
    'charts updated ' + istNow().toISOString().substr(11, 8) + ' IST';
}

/* ---------------------------------------------- portfolio (every 30s) */

function setLive(id, text) {
  const el = document.getElementById(id);
  if (!el || el.textContent === text) return;
  el.textContent = text;
  el.classList.remove('just-updated');
  void el.offsetWidth;              // restart the animation
  el.classList.add('just-updated');
}

async function refreshPortfolio() {
  try {
    const response = await fetch('/api/portfolio_summary');
    const data = await response.json();
    if (!data.ok) return;

    const rupees = (n) => 'Rs ' + Math.round(n).toLocaleString('en-IN');

    setLive('syncCash', rupees(data.cash));
    setLive('syncHoldings', rupees(data.total_value));
    setLive('syncNet', rupees(data.net_worth));
    setLive('syncProfit', (data.total_profit >= 0 ? '+' : '') + rupees(data.total_profit));

    const profitEl = document.getElementById('syncProfit');
    if (profitEl) {
      profitEl.className = data.total_profit > 0 ? 'up'
                         : data.total_profit < 0 ? 'down' : 'flat';
    }

    const cashEl = document.getElementById('cashDisplay');
    if (cashEl) cashEl.textContent = rupees(data.cash);

    // Standing orders are checked on this same 30-second cycle.
    if (data.rules) renderRules(data.rules);
    announceFired(data.fired);

    document.getElementById('syncStamp').textContent =
      'portfolio updated ' + istNow().toISOString().substr(11, 8) + ' IST';

  } catch (error) { /* a missed update is harmless; the next one retries */ }
}

/* The price gap between the two exchanges for the same share. */
function showSpread(nsePrice, bsePrice) {
  const el = document.getElementById('spreadNote');
  if (!nsePrice || !bsePrice) {
    el.textContent = '';
    return;
  }
  const gap = bsePrice - nsePrice;
  const pct = (gap / nsePrice * 100).toFixed(2);
  el.innerHTML = `NSE Rs ${nsePrice.toLocaleString('en-IN')} vs `
    + `BSE Rs ${bsePrice.toLocaleString('en-IN')} — `
    + `<strong class="${gap >= 0 ? 'up' : 'down'}">`
    + `${gap >= 0 ? '+' : ''}${gap.toFixed(2)} (${pct}%)</strong> spread`;
}

async function refreshPrices() {
  try {
    const response = await fetch('/api/prices');
    const data = await response.json();
    if (!data.ok) return;

    data.prices.forEach((row) => {
      const chip = document.querySelector(`.ticker-chip[data-base="${row.base}"]`);
      if (!chip || row.price === null) return;

      chip.querySelector('[data-price]').textContent = row.price.toLocaleString('en-IN');

      const changeEl = chip.querySelector('[data-change]');
      changeEl.textContent = (row.change_pct >= 0 ? '+' : '') + row.change_pct + '%';
      changeEl.className = 'tchange ' +
        (row.change_pct > 0 ? 'up' : row.change_pct < 0 ? 'down' : 'flat');
    });
  } catch (error) { /* a failed refresh is harmless; the next one retries */ }
}

/* ---------------------------------------------- running the agents */

async function runAnalysis() {
  const button = document.getElementById('analyzeBtn');
  button.disabled = true;
  document.getElementById('loading').classList.remove('hidden');
  document.getElementById('results').classList.add('hidden');

  try {
    const response = await fetch('/api/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        ticker: symbolFor(activeBase, window.PRIMARY),
        interval: currentInterval,
        break_news: document.getElementById('breakNews').checked,
        break_prices: document.getElementById('breakPrices').checked
      })
    });

    const data = await response.json();
    if (!data.ok) {
      alert('Analysis failed: ' + data.error);
      return;
    }

    lastRecoId = data.reco_id;
    renderSignals(data.signals);
    renderAgents(data.agents);
    renderVerdict(data.verdict);
    renderSources(data.documents);
    renderMetrics(data.metrics);

    document.getElementById('results').classList.remove('hidden');
    document.getElementById('results').scrollIntoView({ behavior: 'smooth' });

  } catch (error) {
    alert('Something went wrong: ' + error);
  } finally {
    button.disabled = false;
    document.getElementById('loading').classList.add('hidden');
  }
}

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text === null || text === undefined ? '' : text;
  return div.innerHTML;
}

function renderSignals(signals) {
  document.getElementById('signalCards').innerHTML =
    ['momentum', 'volume', 'sentiment'].map((key) => {
      const s = signals[key];
      const value = s.value === null ? 'n/a' : s.value;
      const cls = ['BULLISH', 'BEARISH'].includes(s.label) ? s.label : 'NEUTRAL';
      return `
        <div class="card">
          <div class="agent-source">${escapeHtml(s.dimension)}</div>
          <div class="agent-signal signal-${cls}">${escapeHtml(s.label)}</div>
          <p style="margin:.4rem 0"><strong>${value}</strong>
            <span style="color:var(--muted); font-size:.85rem">${escapeHtml(s.unit)}</span></p>
          <div class="meter"><span style="width:${(s.confidence * 100).toFixed(0)}%"></span></div>
          <p style="font-size:.82rem">${escapeHtml(s.detail)}</p>
        </div>`;
    }).join('');
}

function renderAgents(agentList) {
  document.getElementById('agentCards').innerHTML = agentList.map((a) => {
    const evidence = (a.evidence || []).map((e) => `<li>${escapeHtml(e)}</li>`).join('');
    return `
      <div class="card agent-card">
        <div class="agent-source">${escapeHtml(a.source)}</div>
        <h3 style="margin:.2rem 0">${escapeHtml(a.agent_name)}</h3>
        <div class="agent-signal signal-${escapeHtml(a.signal)}">${escapeHtml(a.signal)}</div>
        <div class="meter"><span style="width:${(a.confidence * 100).toFixed(0)}%"></span></div>
        <div style="display:flex; justify-content:space-between; font-size:.76rem">
          <span style="color:var(--muted)">confidence ${(a.confidence * 100).toFixed(0)}%</span>
          <span class="quality-tag quality-${escapeHtml(a.data_quality)}">${escapeHtml(a.data_quality)}</span>
        </div>
        <p style="font-size:.88rem; margin-top:.6rem">${escapeHtml(a.reasoning)}</p>
        <details><summary>Evidence used</summary><ul>${evidence}</ul></details>
        <p style="color:var(--muted); font-size:.74rem; margin-top:.5rem">
          responded in ${a.latency_ms} ms</p>
      </div>`;
  }).join('');
}

function renderVerdict(verdict) {
  const warnings = (verdict.risk_warnings || [])
    .map((w) => `<div class="warn">${escapeHtml(w)}</div>`).join('');

  document.getElementById('verdictArea').innerHTML = `
    <div class="verdict-card">
      <div>
        <div class="verdict-big verdict-${escapeHtml(verdict.verdict)}">${escapeHtml(verdict.verdict)}</div>
        <div class="meter"><span style="width:${(verdict.confidence * 100).toFixed(0)}%"></span></div>
        <p style="color:var(--muted); font-size:.85rem">
          confidence ${(verdict.confidence * 100).toFixed(0)}%</p>
      </div>
      <div>
        <h3 style="font-size:1.4rem">${escapeHtml(verdict.headline)}</h3>
        <p>${escapeHtml(verdict.reasoning)}</p>
        <p style="color:var(--blue-light); font-size:.9rem">
          <strong>Why this is personal to you:</strong>
          ${escapeHtml(verdict.personalisation_note || '')}</p>
        ${warnings}
        <div class="decision-row">
          <button class="btn btn-red" onclick="recordChoice('FOLLOWED')">Follow this advice</button>
          <button class="btn btn-ghost" onclick="recordChoice('IGNORED')">Ignore it</button>
          <span id="choiceMsg" style="align-self:center; color:var(--green); font-size:.88rem"></span>
        </div>
      </div>
    </div>`;
}

function renderSources(documents) {
  if (!documents || documents.length === 0) {
    document.getElementById('sourceArea').innerHTML =
      '<div class="warn">No source documents were retrieved for this query.</div>';
    return;
  }
  document.getElementById('sourceArea').innerHTML = documents.map((d) => `
    <div class="source-doc">
      <h4>${escapeHtml(d.filename)} — relevance ${d.score}</h4>
      <pre>${escapeHtml(d.snippet)}</pre>
    </div>`).join('');
}

function renderMetrics(metrics) {
  const cards = [
    ['Parallel agent latency', metrics.parallel_ms + ' ms', 'All three agents together'],
    ['If run one by one', metrics.sequential_ms + ' ms', 'What sequential would have cost'],
    ['Synthesis latency', metrics.synthesis_ms + ' ms', 'The fourth agent'],
    ['Agent consensus', (metrics.consensus * 100).toFixed(0) + '%', 'How many agents agreed']
  ];
  document.getElementById('metricArea').innerHTML = cards.map(([label, value, hint]) => `
    <div class="card">
      <div class="agent-source">${escapeHtml(label)}</div>
      <div style="font-family:'Bebas Neue'; font-size:2.1rem">${escapeHtml(value)}</div>
      <p style="font-size:.78rem">${escapeHtml(hint)}</p>
    </div>`).join('');
}

/* ---------------------------------------------- user decisions */

async function recordChoice(action) {
  if (!lastRecoId) return;
  try {
    const response = await fetch('/api/action', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reco_id: lastRecoId, action: action })
    });
    const data = await response.json();
    const message = document.getElementById('choiceMsg');

    if (data.ok) {
      message.textContent = action === 'FOLLOWED'
        ? 'Recorded — we will score this call against what the price actually does.'
        : 'Recorded as ignored. It still counts toward the AI scorecard.';
    } else {
      message.style.color = 'var(--red)';
      message.textContent = 'Could not save: ' + data.error;
    }
  } catch (error) { /* not worth blocking the demo over */ }
}

async function trade(action) {
  const quantity = parseInt(document.getElementById('qty').value, 10);
  const box = document.getElementById('tradeMsg');

  if (!quantity || quantity < 1) {
    box.innerHTML = '<div class="warn">Enter a quantity of at least 1.</div>';
    return;
  }

  try {
    const response = await fetch('/api/trade', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        ticker: symbolFor(activeBase, window.PRIMARY),
        action: action,
        quantity: quantity
      })
    });
    const data = await response.json();

    if (data.ok) {
      box.innerHTML = `<div class="flash flash-success">${escapeHtml(data.message)}</div>`;
      // Reprice immediately so the position strip matches the trade you just made.
      refreshPortfolio();
    } else {
      box.innerHTML = `<div class="flash flash-error">${escapeHtml(data.error)}</div>`;
    }
  } catch (error) {
    box.innerHTML = `<div class="flash flash-error">Trade failed: ${escapeHtml(error)}</div>`;
  }
}

/* ---------------------------------------------- automatic buy / sell */

function renderRules(rules) {
  const box = document.getElementById('rulesList');
  if (!box) return;

  if (!rules || rules.length === 0) {
    box.innerHTML = '<p class="auto-note">No rules set yet. '
      + 'Choose one above and press Set rule.</p>';
    return;
  }

  box.innerHTML = rules.map((r) => `
    <div class="rule-card rule-${escapeHtml(r.action)}">
      <div>
        <div class="rule-name">${escapeHtml(r.label)} · ${escapeHtml(r.ticker)}</div>
        <div class="rule-detail">${escapeHtml(r.note)} · ${r.quantity} shares</div>
      </div>
      <div class="rule-progress"><span style="width:${r.progress}%"></span></div>
      <div class="rule-detail" style="min-width:190px">${escapeHtml(r.explanation)}</div>
      <button class="btn btn-ghost" style="padding:.3rem .8rem; font-size:.76rem"
              onclick="cancelRule(${r.id})">Cancel</button>
    </div>`).join('');
}

async function loadRules() {
  try {
    const response = await fetch('/api/rules');
    const data = await response.json();
    if (data.ok) renderRules(data.rules);
  } catch (error) { /* ignore */ }
}

async function createRule() {
  const box = document.getElementById('tradeMsg');

  try {
    const response = await fetch('/api/rules', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        ticker: symbolFor(activeBase, window.PRIMARY),
        rule_type: document.getElementById('ruleType').value,
        target_pct: document.getElementById('ruleTarget').value,
        quantity: document.getElementById('ruleQty').value
      })
    });
    const data = await response.json();

    if (data.ok) {
      renderRules(data.rules);
      box.innerHTML = '<div class="flash flash-success">Rule set. '
        + 'It will run automatically while this app is open.</div>';
    } else {
      box.innerHTML = `<div class="flash flash-error">${escapeHtml(data.error)}</div>`;
    }
  } catch (error) {
    box.innerHTML = `<div class="flash flash-error">Could not set rule: ${escapeHtml(error)}</div>`;
  }
}

async function cancelRule(ruleId) {
  try {
    const response = await fetch(`/api/rules/${ruleId}/cancel`, { method: 'POST' });
    const data = await response.json();
    if (data.ok) renderRules(data.rules);
  } catch (error) { /* ignore */ }
}

/* Tell the user when a rule actually fired. */
function announceFired(fired) {
  if (!fired || fired.length === 0) return;

  document.getElementById('tradeMsg').innerHTML = fired.map((f) => {
    const good = f.status === 'TRIGGERED';
    return `<div class="flash flash-${good ? 'success' : 'error'}">
      <strong>${escapeHtml(f.label)} ${good ? 'fired' : 'could not run'}
      on ${escapeHtml(f.ticker)}:</strong> ${escapeHtml(f.message)}</div>`;
  }).join('');
}

/* ---------------------------------------------- wiring it all up */

function startAutoRefresh() {
  if (refreshTimer) clearInterval(refreshTimer);
  if (portfolioTimer) clearInterval(portfolioTimer);

  refreshTimer = setInterval(refreshAll, REFRESH_MS);
  portfolioTimer = setInterval(refreshPortfolio, PORTFOLIO_MS);
}

document.addEventListener('DOMContentLoaded', () => {
  charts.NSE = buildChart('NSE', 'chartNSE');
  charts.BSE = buildChart('BSE', 'chartBSE');

  tickClock();
  setInterval(tickClock, 1000);

  // Load straight away rather than waiting for the first timer to fire.
  refreshAll();
  startAutoRefresh();

  // Coming back to this tab after it was hidden: refresh at once, because
  // whatever is on screen is now stale.
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) {
      refreshAll();
      startAutoRefresh();
    }
  });

  // Same when the window regains focus or the network comes back.
  window.addEventListener('focus', refreshPortfolio);
  window.addEventListener('online', () => { refreshAll(); startAutoRefresh(); });

  document.getElementById('intervalSelect').addEventListener('change', (event) => {
    currentInterval = event.target.value;
    document.getElementById('refreshEvery').textContent =
      `${currentInterval === '2m' ? '2-minute' : '15-minute'} candles, refreshing every 2 min`;
    refreshAll();
    startAutoRefresh();
  });

  document.querySelectorAll('.ticker-chip').forEach((chip) => {
    chip.addEventListener('click', () => {
      document.querySelectorAll('.ticker-chip').forEach((c) => c.classList.remove('active'));
      chip.classList.add('active');
      activeBase = chip.dataset.base;
      document.getElementById('results').classList.add('hidden');
      refreshAll();
      startAutoRefresh();   // restart the clock so you get a full 2 minutes
    });
  });

  document.getElementById('refreshNow').addEventListener('click', () => {
    refreshAll();
    startAutoRefresh();
  });

  // Changing the risk profile is the demo's key moment: switch it, re-run
  // the same stock, and the verdict changes on identical market data.
  document.getElementById('riskSelect').addEventListener('change', async (event) => {
    try {
      await fetch('/api/risk', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ risk: event.target.value })
      });
      document.getElementById('tradeMsg').innerHTML =
        '<div class="flash flash-success">Risk profile set to ' + event.target.value
        + '. Run the agents again to see the verdict change.</div>';
    } catch (error) { /* ignore */ }
  });

  // Automatic buy / sell is opt-in: the form stays hidden until you enable it.
  const autoEnable = document.getElementById('autoEnable');
  const autoForm = document.getElementById('autoForm');

  // Remember the choice so it survives a page reload.
  try {
    if (localStorage.getItem('autoEnabled') === 'yes') {
      autoEnable.checked = true;
      autoForm.classList.remove('hidden');
      loadRules();
    }
  } catch (error) { /* private browsing - just start disabled */ }

  autoEnable.addEventListener('change', () => {
    autoForm.classList.toggle('hidden', !autoEnable.checked);
    try {
      localStorage.setItem('autoEnabled', autoEnable.checked ? 'yes' : 'no');
    } catch (error) { /* ignore */ }
    if (autoEnable.checked) loadRules();
  });

  document.getElementById('createRule').addEventListener('click', createRule);

  document.getElementById('analyzeBtn').addEventListener('click', runAnalysis);
  document.getElementById('buyBtn').addEventListener('click', () => trade('BUY'));
  document.getElementById('sellBtn').addEventListener('click', () => trade('SELL'));
  document.getElementById('breakPrices').addEventListener('change', refreshAll);
});

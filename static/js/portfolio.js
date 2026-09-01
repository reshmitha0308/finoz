/* ==========================================================
   portfolio.js
   ----------------------------------------------------------
   Keeps the portfolio page alive without reloading it.

   Every 30 seconds it asks the backend to reprice every
   holding, then updates the numbers in place. The prices come
   from the SAME cache the candlestick charts use, so what you
   see here always matches what you see on the market page.

   It also refreshes the moment you come back to the tab, so
   you are never looking at a stale number.
   ========================================================== */

const PORTFOLIO_MS = 30 * 1000;   // 30 seconds
let portfolioTimer = null;

/* ---------------------------------------------- IST clock */

function istNow() {
  const now = new Date();
  return new Date(now.getTime() + 5.5 * 60 * 60 * 1000);
}

function istTimeString() {
  return istNow().toISOString().substr(11, 8);
}

function tickClock() {
  const el = document.getElementById('istClock');
  if (el) el.textContent = istTimeString();
}

/* ---------------------------------------------- updating values */

const rupees = (n) => 'Rs ' + Math.round(n).toLocaleString('en-IN');
const rupees2 = (n) => 'Rs ' + n.toLocaleString('en-IN', {
  minimumFractionDigits: 2, maximumFractionDigits: 2
});

/* Only touch the page when a value actually changed, and flash it so the
   user can see which number moved. */
function setLive(el, text, className) {
  if (!el || el.textContent.trim() === text.trim()) return;
  el.textContent = text;
  if (className !== undefined) el.className = className;
  el.classList.remove('just-updated');
  void el.offsetWidth;              // restart the CSS animation
  el.classList.add('just-updated');
}

function profitClass(value) {
  return value > 0 ? 'up' : value < 0 ? 'down' : 'flat';
}

async function refreshPortfolio() {
  const stamp = document.getElementById('pfStamp');

  try {
    const response = await fetch('/api/portfolio_summary');
    const data = await response.json();

    if (!data.ok) {
      if (stamp) stamp.textContent = 'update failed: ' + data.error;
      return;
    }

    setLive(document.getElementById('pfCash'), rupees(data.cash));
    setLive(document.getElementById('pfValue'), rupees(data.total_value));
    setLive(document.getElementById('pfNet'), rupees(data.net_worth));
    setLive(document.getElementById('pfProfit'), rupees(data.total_profit),
            profitClass(data.total_profit));

    // Update each holding row in place, matched by its ticker.
    data.holdings.forEach((holding) => {
      const row = document.querySelector(`tr[data-ticker="${holding.ticker}"]`);
      if (!row) return;

      setLive(row.querySelector('[data-qty]'), String(holding.quantity));
      setLive(row.querySelector('[data-current]'), rupees2(holding.current_price));
      setLive(row.querySelector('[data-value]'), rupees2(holding.value));
      setLive(row.querySelector('[data-profit]'),
              `${rupees2(holding.profit)} (${holding.profit_pct}%)`,
              profitClass(holding.profit));
    });

    // A holding that has been fully sold should disappear.
    const stillHeld = new Set(data.holdings.map((h) => h.ticker));
    document.querySelectorAll('#holdingsBody tr[data-ticker]').forEach((row) => {
      if (!stillHeld.has(row.dataset.ticker)) row.remove();
    });

    // Standing orders are checked on this same 30-second cycle.
    renderRules(data.rules);
    if (data.fired && data.fired.length) {
      // A rule just bought or sold. Reload so the tables below are correct.
      setTimeout(() => window.location.reload(), 1200);
    }

    if (stamp) stamp.textContent = 'updated ' + istTimeString() + ' IST';

  } catch (error) {
    if (stamp) stamp.textContent = 'offline — will retry in 30s';
  }
}

/* ---------------------------------------------- automatic rules */

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text === null || text === undefined ? '' : text;
  return div.innerHTML;
}

function renderRules(rules) {
  const box = document.getElementById('rulesList');
  if (!box || !rules) return;

  if (rules.length === 0) {
    box.innerHTML = '<div class="card"><p>No active rules. '
      + '<a href="/dashboard">Set one on the market page</a> to have the app '
      + 'buy or sell for you when a target is reached.</p></div>';
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

async function cancelRule(ruleId) {
  try {
    const response = await fetch(`/api/rules/${ruleId}/cancel`, { method: 'POST' });
    const data = await response.json();
    if (data.ok) renderRules(data.rules);
  } catch (error) { /* ignore */ }
}

/* ---------------------------------------------- wiring */

function startAutoRefresh() {
  if (portfolioTimer) clearInterval(portfolioTimer);
  portfolioTimer = setInterval(refreshPortfolio, PORTFOLIO_MS);
}

document.addEventListener('DOMContentLoaded', () => {
  tickClock();
  setInterval(tickClock, 1000);

  refreshPortfolio();        // straight away, do not wait 30 seconds
  startAutoRefresh();

  // Coming back to this tab: whatever is on screen is stale, so refresh now.
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) {
      refreshPortfolio();
      startAutoRefresh();
    }
  });

  window.addEventListener('focus', refreshPortfolio);
  window.addEventListener('online', refreshPortfolio);
});

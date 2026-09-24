/* TriVega Dashboard — auto-refresh + render logic */

let pnlChart = null;
let currentTab = 'paper';
let allActivity = [];
// Three independent filter axes. Was a single vertical string, which could not answer
// "show me only Clockwork" — the question the feed kept failing.
let actFilter = { book: 'ALL', ev: 'ALL', days: 5 };
let calView = 'earnings';

// ── Bootstrap ─────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  loadData();
  wireCloseControls();
  wireActivityFilters();
  setInterval(loadData, 30000);
});

// ── Data fetch ────────────────────────────────────────────
async function loadData() {
  setRefreshing(true);
  try {
    const resp = await fetch('/api/data');
    const d = await resp.json();
    renderAll(d);
  } catch (e) {
    console.error('Fetch failed:', e);
  } finally {
    setRefreshing(false);
  }
}

function setRefreshing(on) {
  const dot = document.getElementById('refresh-dot');
  if (dot) dot.style.color = on ? '#d29922' : '#3fb950';
}

// ── Tab switching ──────────────────────────────────────────
function switchTab(tab) {
  currentTab = tab;
  document.getElementById('tab-paper').classList.toggle('active', tab === 'paper');
  document.getElementById('tab-prod').classList.toggle('active',  tab === 'prod');
  document.getElementById('paper-content').style.display = tab === 'paper' ? '' : 'none';
  document.getElementById('prod-content').style.display  = tab === 'prod'  ? '' : 'none';
}

// ── Calendar sub-tab ───────────────────────────────────────
function showCal(view) {
  calView = view;
  document.querySelectorAll('.cal-tab').forEach(b => b.classList.remove('active'));
  event.target.classList.add('active');
  document.getElementById('earnings-cal').style.display = view === 'earnings' ? '' : 'none';
  document.getElementById('macro-cal').style.display    = view === 'macro'    ? '' : 'none';
}

// ── Activity filters ───────────────────────────────────────
// Which books each group chip covers. EQUITY is the four books that share the real
// equity account; FUTURES is the NY book plus London, which is a separate book with
// its own exits (FUT CLOSE does not cover it).
const BOOK_GROUPS = {
  EQUITY:  ['Day Trader', 'Wave Rider', 'Contrarian', 'Clockwork'],
  FUTURES: ['Futures NY', 'London'],
};

function setActFilter(axis, value) {
  actFilter[axis] = (axis === 'days') ? Number(value) : value;
  document.querySelectorAll(`.filter-btn[data-f="${axis}"]`).forEach(b =>
    b.classList.toggle('active', b.dataset.v === String(value)));
  renderActivityFeed(allActivity);
}

// Kept as a global because the summary-card book chips call it to jump straight to one
// engine's legs — that is the drill-down path from "Clockwork -$180" to the rows behind it.
function filterActivityByBook(book) {
  setActFilter('book', book);
  document.getElementById('activity-feed')
    ?.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

function wireActivityFilters() {
  document.addEventListener('click', ev => {
    const b = ev.target.closest('.filter-btn[data-f]');
    if (b) { setActFilter(b.dataset.f, b.dataset.v); return; }
    // Drill-down from the summary card's per-book figures.
    const link = ev.target.closest('a.book-link');
    if (link) {
      ev.preventDefault();
      // The card reports TODAY, so the feed opens on today to match. Showing five
      // days under a one-day number is how a drill-down stops reconciling.
      setActFilter('days', 1);
      filterActivityByBook(link.dataset.book);
    }
  });
}

// ── Master render ──────────────────────────────────────────

// ══════════════════════════════════════════════════════════════════════════════
// CLOSE CONTROLS
// ══════════════════════════════════════════════════════════════════════════════
// Nothing here places an order. Each button POSTs an intent to /api/close; the process
// that OWNS that book picks it up and closes through its own tested exit path. We then
// poll for the real outcome, because "the button was pressed" is not the same thing as
// "the position closed" — and reporting the first as the second is how you end up
// believing a book is flat when it is not.
let CSRF = null;
let _pendingClose = null;

function closeBtn(p) {
  // A row whose exit order is already working must not offer another one. Clockwork's
  // PENDING_EXIT rows have an MOO in the market; sending a second sell is precisely the
  // Sep 10-11 2026 oversell.
  const st = p.row_status || 'OPEN';
  if (st !== 'OPEN') {
    return `<button class="sell-btn" disabled title="An exit order is already working on this position (${st}) — sending another would oversell it.">${st === 'PENDING_EXIT' ? 'exiting' : st.toLowerCase()}</button>`;
  }
  const label = (p.side === 'SHORT') ? 'Cover' : 'Sell';
  const spec = encodeURIComponent(JSON.stringify(p));
  return `<button class="sell-btn" data-close="${spec}" title="Ask the owning book to close this position now">${label}</button>`;
}

function openCloseModal(opts) {
  _pendingClose = opts;
  document.getElementById('close-modal-title').textContent = opts.title;
  document.getElementById('close-modal-body').innerHTML = opts.body;
  const pw = document.getElementById('close-modal-pwwrap');
  pw.hidden = !opts.needPassword;
  document.getElementById('close-modal-pw').value = '';
  const err = document.getElementById('close-modal-err');
  err.hidden = true; err.textContent = '';
  const go = document.getElementById('close-modal-go');
  go.textContent = opts.cta || 'Close position';
  go.disabled = false;
  document.getElementById('close-modal').hidden = false;
}

function hideCloseModal() {
  document.getElementById('close-modal').hidden = true;
  _pendingClose = null;
}

async function submitClose() {
  if (!_pendingClose) return;
  const go  = document.getElementById('close-modal-go');
  const err = document.getElementById('close-modal-err');
  const payload = Object.assign({ csrf: CSRF }, _pendingClose.payload);
  if (_pendingClose.needPassword) {
    payload.password = document.getElementById('close-modal-pw').value;
    if (!payload.password) {
      err.hidden = false; err.textContent = 'Enter the dashboard password to confirm.';
      return;
    }
  }
  go.disabled = true; go.textContent = 'Sending…';
  try {
    const r = await fetch('/api/close', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload)
    });
    const d = await r.json();
    if (!d.ok) {
      err.hidden = false; err.textContent = d.error || 'request refused';
      go.disabled = false; go.textContent = _pendingClose.cta || 'Close position';
      return;
    }
    hideCloseModal();
    toastClose(d.message, 'pending');
    pollCloseResult(d.request_id, d.owner);
  } catch (e) {
    err.hidden = false; err.textContent = String(e);
    go.disabled = false;
  }
}

async function pollCloseResult(id, owner) {
  // Poll until the owner reports back, or until the request's own TTL has passed.
  // A request that expires unexecuted is reported as such rather than silently forgotten.
  for (let i = 0; i < 40; i++) {
    await new Promise(r => setTimeout(r, 3000));
    try {
      const d = await (await fetch('/api/close/status?ids=' + id)).json();
      const req = (d.requests || [])[0];
      if (!req) continue;
      if (req.status === 'DONE') {
        toastClose(`${owner}: ${req.result || 'closed'}`, 'done'); loadData(); return;
      }
      if (req.status === 'FAILED') {
        toastClose(`${owner} could not close: ${req.result}`, 'fail'); loadData(); return;
      }
      if (req.status === 'EXPIRED') {
        toastClose(`${owner} never picked it up — ${req.result}. Nothing was sent to the broker.`, 'fail');
        return;
      }
    } catch (e) { /* keep polling */ }
  }
  toastClose(`${owner}: no outcome reported yet — check the service log before assuming it closed.`, 'fail');
}

function toastClose(msg, kind) {
  let box = document.getElementById('close-toasts');
  if (!box) {
    box = document.createElement('div');
    box.id = 'close-toasts'; box.className = 'close-toasts';
    document.body.appendChild(box);
  }
  const t = document.createElement('div');
  t.className = 'close-toast ' + kind;
  t.textContent = msg;
  box.appendChild(t);
  setTimeout(() => t.remove(), kind === 'pending' ? 8000 : 20000);
}

function wireCloseControls() {
  // Row buttons are delegated, so they survive every table re-render.
  document.addEventListener('click', (ev) => {
    const b = ev.target.closest('button[data-close]');
    if (!b) return;
    const p = JSON.parse(decodeURIComponent(b.dataset.close));
    const what = p.kind === 'options'
      ? `${p.symbol} ${p.strategy || ''} · ${p.contracts} contract(s)`
      : (p.kind === 'futures'
         ? `${p.account_mode} ${p.session} · ${p.side} ${p.qty} ${p.symbol}`
         : `${p.symbol} · ${p.shares} shares`);
    openCloseModal({
      title: `Close ${p.symbol}?`,
      body: `<strong>${what}</strong><br><span class="muted-text">${p.book || p.owner_label || ''}</span>`,
      needPassword: false,
      cta: (p.side === 'SHORT') ? 'Cover now' : 'Close now',
      payload: {
        action: 'CLOSE_ONE', target: p.target, symbol: p.symbol,
        trade_id: p.id, book: p.book, account_mode: p.account_mode || null
      }
    });
  });

  const bulk = [
    ['closeall-equity', 'equity', null, 'Close the whole Day Trader book?',
     'Only the <strong>Day Trader</strong> book. Wave Rider, Contrarian and Clockwork are separate books and are <strong>not</strong> touched — close those from their own rows.'],
    ['closeall-options', 'options', null, 'Close every options position?',
     'Every open options position, closed through options_trader\'s own two-leg path.'],
    ['closeall-fut-ibkr', 'futures_ny', 'IBKR', 'Flatten NY futures on IBKR?',
     'The <strong>NY</strong> book on <strong>IBKR</strong> only. London is a separate book and is <strong>not</strong> included.'],
    ['closeall-fut-tc', 'futures_ny', 'TC', 'Flatten NY futures on TC?',
     'The <strong>NY</strong> book on <strong>TC</strong> only. London is a separate book and is <strong>not</strong> included.'],
  ];
  bulk.forEach(([id, target, acct, title, body]) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.addEventListener('click', () => openCloseModal({
      title, body, needPassword: true, cta: 'Yes — close them',
      payload: { action: 'CLOSE_ALL', target, account_mode: acct }
    }));
  });

  document.getElementById('close-modal-cancel')?.addEventListener('click', hideCloseModal);
  document.getElementById('close-modal-go')?.addEventListener('click', submitClose);
  document.getElementById('close-modal')?.addEventListener('click', (e) => {
    if (e.target.id === 'close-modal') hideCloseModal();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') hideCloseModal();
    if (e.key === 'Enter' && _pendingClose) submitClose();
  });
}

function renderAll(d) {
  CSRF = d.csrf || CSRF;
  document.getElementById('last-refresh').textContent = d.ts || '—';
  renderModeBadge(d.mode);
  renderServices(d.services);
  renderRegime(d.regime);
  renderSessionPnl(d.eq_summary, d.opt_summary, d.fut_ibkr_summary, d.fut_tc_summary);
  renderSummaryCards(d.eq_summary, d.opt_summary, d.fut_ibkr_summary, d.fut_tc_summary);
  renderPnlChart(d.pnl_by_book);
  renderScorecard(d.scorecard);
  renderEquityTable(d.equity_positions);
  renderOptionsTable(d.options_positions, d.system_health?.options);
  renderFuturesTable(d.futures_positions, d.futures_session);
  renderSectors(d.sector_grades);
  renderSystemHealth(d.system_health);
  renderEngines(d.engines);
  renderAlerts(d.alerts);
  renderCalendar(d.earnings_calendar, d.macro_calendar);
  allActivity = d.activity || [];
  renderActivityFeed(allActivity);
  renderProdChecklist(d.golive_checklist);
}

// ── Engine scoreboard (Sep 11 2026) ──────────────────────────────────────
// One row per live equity book. "own exits" = closed by the engine's OWN rule vs by reconcile
// or by hand; while that is not ~1.0 the P&L beside it describes the plumbing, not the strategy.
function renderEngines(rows) {
  const el = document.getElementById('engines-table');
  if (!el) return;
  if (!rows || !rows.length) { el.innerHTML = '<div class="empty-msg">no engine data</div>'; return; }
  const money = v => `<span class="${v > 0 ? 'pos' : v < 0 ? 'neg' : ''}">${v > 0 ? '+' : ''}$${(v || 0).toFixed(2)}</span>`;
  const selfCell = s => {
    if (!s) return '<span class="muted-text" title="this table has no exit_reason column">—</span>';
    const [a, b] = s.split('/').map(Number);
    const cls = b === 0 ? '' : (a === b ? 'pos' : (a === 0 ? 'neg' : 'warn'));
    const tip = a === b ? 'every exit was the engine\'s own rule — this data is trustworthy'
                        : 'some/all exits came from reconcile or a manual close, NOT the strategy';
    return `<span class="${cls}" title="${tip}">${s}</span>`;
  };
  el.innerHTML = `<table class="positions-table"><thead><tr>
      <th>engine</th><th title="Positions this book is holding right now">open</th>
      <th title="Trades this book CLOSED today, and the realised P&L from them. Open positions are not counted here.">today</th>
      <th title="Trades closed in the last 7 days and their realised P&L">7-day</th>
      <th title="Share of the last 7 days' closed trades that made money">win</th>
      <th title="exits the engine made itself vs forced by reconcile/manual">own exits</th>
      <th>last</th></tr></thead><tbody>` +
    rows.map(r => `<tr title="${r.desc || ''}">
      <td>${engBadge(r.engine)}${r.err ? ' <span class="neg" title="' + r.err + '">!</span>' : ''}
          <div class="engine-analogy">${r.analogy || ''}</div></td>
      <td>${r.open}</td>
      <td>${tradeCount(r.today_n)} ${money(r.today_pnl)}</td>
      <td>${tradeCount(r.wk_n)} ${money(r.wk_pnl)}</td>
      <td>${r.wk_win == null ? '—' : r.wk_win + '%'}</td>
      <td>${selfCell(r.self_exits)}</td>
      <td class="muted-text">${r.last || '—'}</td></tr>`).join('') +
    `</tbody></table>`;
}

// ── System health panel (Book Health / funnel / Trade Cop / Mirror Book) ──
// Hover any label or chip for the plain-English explanation.
function renderSystemHealth(h) {
  const el = document.getElementById('system-health');
  if (!el) return;
  if (!h) { el.innerHTML = '<div class="empty-msg">no health data</div>'; return; }
  document.getElementById('sh-universe').textContent = (h.universe || '—') + ' names';

  // Sep 22 2026: a side that stops producing A+ signals freezes its Book Health
  // window, and the panel kept reporting a months-old verdict as if it were live —
  // SHORT was showing "ON +0.71%/sig" from data last written 2026-07-31. The age of
  // the reading is now on the chip itself.
  const bookChip = (name, b) => {
    if (!b) return '';
    const cls = b.stale ? 'warn' : (b.state === 'ON' ? 'pos' : (b.state === 'OFF' ? 'neg' : ''));
    const drift = b.drift == null ? '' : ` ${b.drift > 0 ? '+' : ''}${b.drift}%/sig`;
    const age = b.stale
      ? ` <span class="stale-flag" title="Newest signal behind this reading is ${b.last_signal}. It is not describing the market now.">STALE ${b.age_days}d</span>`
      : '';
    return `<span class="health-chip ${cls}" title="${b.desc || ''}"><b>${name}</b> ${b.state}${drift}</span>${age}`;
  };
  const booksNote = (h.books?.LONG?.state === 'OFF' && h.books?.SHORT?.state === 'OFF')
    ? '<span class="health-detail">both books standing down — own signals not working; flat is intentional</span>'
    : '';

  const f = h.funnel || {};
  // fut_gates rows: [code, count, glossaryName, tooltip]
  const gates = (f.fut_gates || []).map(g =>
    `<span class="gate-chip" title="${g[3] || ''} (log code: ${g[0]})">${g[2] || g[0]} ×${g[1]}</span>`
  ).join(' ');
  const entered = f.fut_entered ?? 0;
  const enteredChip = `<span class="health-chip ${entered > 0 ? 'pos' : ''}" title="Signals that passed every gate and became trades today">${entered} entered</span>`;

  const p = h.parity || {};
  const pCls = p.status === 'OK' ? 'pos' : (p.status ? 'neg' : '');
  const s = h.shadow || {};

  el.innerHTML = `
    <div class="health-row">
      <span class="health-label" title="Book Health Selector: each side trades only while its own recent A+ signals show positive follow-through">Books</span>
      ${bookChip('LONG', h.books && h.books.LONG)} ${bookChip('SHORT', h.books && h.books.SHORT)}
      ${booksNote}
    </div>
    <div class="health-row">
      <span class="health-label" title="A+ grade equity signals seen today, whether or not a trade was taken">Signals today</span>
      <span>A+ equity: ${f.eq_aplus_long ?? 0} long / ${f.eq_aplus_short ?? 0} short</span>
    </div>
    <div class="health-row">
      <span class="health-label" title="MNQ signal funnel today: how many entries got through, and which gate rejected the rest">Futures funnel</span>
      ${enteredChip}
      <span class="health-detail-inline">${gates || 'no blocks logged'}</span>
    </div>
    <div class="health-row">
      <span class="health-label" title="Nightly parity check: replays today through the backtest engine and diffs its trades against what live actually did">Trade Cop</span>
      <span class="health-chip ${pCls}" title="${p.detail || ''}">${p.status || 'no run yet'}</span>
      <span class="health-detail">${p.friendly || p.detail || ''}</span>
    </div>
    <div class="health-row">
      <span class="health-label" title="Shadow-only book that fades the Black Box Recorder's LONG signals. Places NO orders — needs 30+ green days before promotion is discussed (review ~Aug 17)">Mirror Book</span>
      <span title="Shadow paper result — 1 MNQ contract equivalent, no real orders. Low-frequency: gaps between entries are normal, but check the date if 14d reads 0.">${s.n ?? 0} shadow trades · ${(s.pts_total ?? 0) >= 0 ? '+' : ''}${s.pts_total ?? 0} pts all-time · ${(s.pts_14d ?? 0) >= 0 ? '+' : ''}${s.pts_14d ?? 0} pts last 14d</span>
    </div>
    ${renderFeed(h.feed)}
    ${renderProp(h.prop)}
    ${renderFleet(h.fleet)}
    ${renderScoring(h.scoring)}
    ${renderOptionsHealth(h.options)}
    ${renderFieldReport(h.field_report)}`;
}

// ── Data feed + watchdog (Sep 22 2026) ─────────────────────────────────────
// Everything downstream reads bars_5m — Book Health, the forward label, the swing
// engines' signals, every backtest. If the feed stops, the whole system degrades
// quietly rather than failing loudly. Heartbeats are written on every trader scan,
// so silence means the LOOP stopped, not that nothing qualified.
function renderFeed(fd) {
  if (!fd || (!fd.bars_last && !(fd.beats || []).length)) return '';
  const stale = (fd.bars_age_days ?? 0) > 1;
  const beats = (fd.beats || []).map(b => {
    // London only runs 03:00-08:59 ET, so an old beat there is expected, not a fault.
    const lon = b.name.indexOf('london') !== -1;
    const bad = b.age_s > 600 && !lon;
    const mins = Math.round(b.age_s / 60);
    const ago = b.age_s < 120 ? `${b.age_s}s` : `${mins}m`;
    return `<span class="gate-chip ${bad ? 'neg' : ''}" title="${b.name} last wrote a heartbeat ${ago} ago.${lon ? ' London only runs 03:00-08:59 ET — a stale beat outside that window is normal.' : ' Over 10 minutes means the scan loop has stopped.'}">${b.name} ${ago}</span>`;
  }).join(' ');
  return `
    <div class="health-row">
      <span class="health-label" title="5-min bars are the input to Book Health, the forward outcome label, the swing engines and every backtest. Heartbeats are written by each trader on every scan — silence means the loop stopped, not that no trade qualified.">Data feed</span>
      <span class="health-chip ${stale ? 'warn' : 'pos'}" title="Newest 5-min bar in market_data.db. Written by collect_bars at 16:30 ET, so during a session the newest bar is yesterday's close — that is normal.">bars thru ${fd.bars_last || '—'}</span>
      <span class="health-detail-inline">${beats || 'no heartbeats'}</span>
    </div>`;
}

// ── Prop-account room (Sep 22 2026) ────────────────────────────────────────
// TC trades under a trailing Max Loss Limit. On Sep 3 it locked itself out of
// trading by $45 and nothing on this page said so — and it cannot earn its way
// back, because it cannot trade. The remaining room IS the warning.
function renderProp(pr) {
  if (!pr || pr.room == null) return '';
  const cls = pr.room < 0 ? 'neg' : (pr.room < 500 ? 'warn' : 'pos');
  const msg = pr.room < 0 ? 'LOCKED OUT' : `$${Math.round(pr.room).toLocaleString()} room`;
  return `
    <div class="health-row">
      <span class="health-label" title="TopStep-style trailing Max Loss Limit on the TC account. check_can_trade() refuses every entry once balance falls below (high-water mark − $2,000 MLL + $300 buffer). It froze here once, by $45, and could not trade its way out.">Prop room (TC)</span>
      <span class="health-chip ${cls}" title="Balance $${pr.balance.toLocaleString()} · high-water $${pr.hwm.toLocaleString()} · MLL floor $${pr.floor.toLocaleString()} · $300 soft buffer on top.">${msg}</span>
      <span class="health-detail-inline">balance $${pr.balance.toLocaleString()} vs floor $${pr.floor.toLocaleString()} · P&L to target ${pr.target >= 0 ? '+' : '−'}$${Math.abs(pr.target).toLocaleString()}</span>
    </div>`;
}

// ── Fleet capital (Sep 22 2026) ────────────────────────────────────────────
// "How much is actually invested" had no answer anywhere on the dashboard, and
// four books sharing one brokerage account makes it genuinely non-obvious.
function renderFleet(fl) {
  if (!fl || !fl.books || !fl.books.length) return '';
  const pct = fl.alloc ? (fl.deployed / fl.alloc * 100) : 0;
  const parts = fl.books.map(b => {
    const cls = b.deployed > b.alloc ? 'neg' : '';
    return `<span class="gate-chip ${cls}" title="${b.name}: ${b.n} open position(s), $${Math.round(b.deployed).toLocaleString()} at cost against a $${Math.round(b.alloc).toLocaleString()} allocation">`
         + `<span class="eng-dot" data-eng="${b.name}"></span>${b.name} $${Math.round(b.deployed).toLocaleString()}</span>`;
  }).join(' ');
  return `
    <div class="health-row">
      <span class="health-label" title="Cost basis of every open equity position, per book, against the capital each book was allocated. Unrealised P&L is not included — this is money committed, not money made.">Fleet capital</span>
      <span class="health-chip ${pct > 100 ? 'neg' : ''}">$${Math.round(fl.deployed).toLocaleString()} of $${Math.round(fl.alloc).toLocaleString()} (${pct.toFixed(0)}%)</span>
      <span class="health-detail-inline">${parts}</span>
    </div>`;
}

// ── Scoring loop (Sep 22 2026) ─────────────────────────────────────────────
// The grader's inputs and the outcome they get scored against. Both have to keep
// accumulating or the weight fit this instrumentation exists for can never run.
// The label is written nightly by com.sushil.trading.scan_forward_label (17:15).
function renderScoring(sc) {
  if (!sc || !sc.graded) return '';
  const lagDays = sc.last_label
    ? Math.round((Date.now() - new Date(sc.last_label + 'T23:59:59')) / 86400000) : null;
  const stale = lagDays != null && lagDays > 3;
  return `
    <div class="health-row">
      <span class="health-label" title="Every graded candidate stores the components that produced its score, and a forward outcome label computed from real bars. Fitting the 15 hand-assigned grader weights against real outcomes needs both — this row shows whether they are still being collected.">Scoring loop</span>
      <span class="health-chip ${stale ? 'warn' : 'pos'}" title="Newest forward label is for ${sc.last_label}. Written nightly at 17:15 after the session's bars land.">label thru ${sc.last_label || '—'}</span>
      <span class="health-detail-inline">${(sc.with_components ?? 0).toLocaleString()} candidates scored · ${(sc.with_label ?? 0).toLocaleString()} labelled · <b>${(sc.trades_scorable ?? 0).toLocaleString()} real trades fittable</b></span>
    </div>`;
}

// Fish Finder health row removed Sep 22 2026 — the engine was decommissioned
// Aug 15 2026 (Alpha Factory pivot) and this renderer had no call site since.


// ── Field Report row (market_context.py — log-only pre-market brief) ────
function renderFieldReport(fr) {
  if (!fr) return '';
  const cls = fr.stance === 'RISK_ON' ? 'pos' : (fr.stance === 'RISK_OFF' ? 'neg' : '');
  const themes = (fr.themes || []).slice(0, 4).join(', ');
  return `
    <div class="health-row">
      <span class="health-label" title="Pre-market Field Report: mechanical trend/levels + one Claude call synthesizing headlines and the event calendar. LOG-ONLY — no gate reads it. Scored nightly vs actual outcomes after ~4 weeks; graduates to a sizing tilt or event stand-down only if it earns it.">Field Report</span>
      <span class="health-chip ${cls}" title="${fr.one_line || ''}">${fr.stance || '?'} (${fr.confidence || '?'})</span>
      <span class="health-detail-inline">${fr.date} · event risk ${fr.event_risk || '?'}${themes ? ' · ' + themes : ''}</span>
    </div>`;
}

// ── Options row inside SYSTEM HEALTH (Jul 18 2026 redesign) ─────────────

function renderOptionsHealth(o) {
  if (!o) return '';
  const c = o.calcs_today || {};
  const w = o.whatif_14d || {};
  const cl = o.closed_14d || {};
  const openList = (o.open || [])
    .map(t => `${t.symbol} ${t.strategy} ($${Math.round(t.premium)})`)
    .join(', ') || 'none';
  const wPnl = w.pnl ?? 0;
  // Sep 22 2026: the circuit breaker blocked EVERY options entry for the whole paper
  // trial and appeared nowhere on this page — the row just read "0 calcs" and looked
  // like a quiet market. A gate that can stop the book must be visible on the book.
  const b = o.breaker || {};
  let brkChip = '';
  const bLim = b.limit ?? 0, bPnl = b.pnl ?? 0;
  if (b.tripped === true) {
    brkChip = `<span class="health-chip neg" title="Realized loss $${Math.abs(bPnl).toLocaleString()} since ${b.since || 'inception'} exceeds the $${bLim.toLocaleString()} limit. NO new entries are possible until this recovers.">BREAKER TRIPPED</span>`;
  } else if (b.tripped === false) {
    const room = (bLim + bPnl);
    brkChip = `<span class="health-chip pos" title="Realized $${bPnl.toLocaleString()} since ${b.since || 'inception'} against a $${bLim.toLocaleString()} limit — $${Math.round(room).toLocaleString()} of room left. Edge Budget is in ${b.gate_mode} mode.">breaker OK · $${Math.round(room).toLocaleString()} room</span>`;
  }
  if (b.frozen) brkChip += ` <span class="health-chip neg" title="EQUITY_ECHO_FROZEN is True — automated options entries are switched off in code.">ENTRIES FROZEN</span>`;
  // Book-level Greeks + concentration moved to the Options Positions card
  // (Aug 4 2026) — they describe the positions, so they live next to them.
  // This row stays focused on the gating/funnel story specifically.
  return `
    <div class="health-row">
      <span class="health-label" title="Options trade only in directions whose equity book is healthy (same Books row above). Funnel = calculator runs today; Ghost Ledger = what the suggestions we did NOT take would have made (scored nightly). Full Greeks + concentration are in the Options Positions card below.">Options</span>
      ${brkChip}
      <span>open: ${openList}</span>
      <span class="health-detail-inline">funnel today: ${c.total ?? 0} calcs / ${c.enter ?? 0} enter · closed 14d: ${cl.n ?? 0} for ${(cl.pnl ?? 0) >= 0 ? '+' : ''}$${cl.pnl ?? 0} · ghost ledger 14d: ${w.n ?? 0} skips ${wPnl >= 0 ? '+' : ''}$${wPnl}</span>
    </div>`;
}

// ── 15-day scorecard (per-book closed-trade stats) ─────────
function renderScorecard(rows) {
  const el = document.getElementById('scorecard');
  if (!el) return;
  if (!rows || rows.length === 0) {
    el.innerHTML = '<div class="empty-state">No closed trades in window</div>';
    return;
  }
  const money = v => `<span class="${v > 0 ? 'pnl-pos' : (v < 0 ? 'pnl-neg' : 'pnl-zero')}">${v >= 0 ? '+' : '−'}$${Math.abs(v).toFixed(0)}</span>`;
  const day = d => d ? d.slice(5).replace('-', '/') : '';
  el.innerHTML = `<table class="positions-table scorecard-table">
    <thead><tr>
      <th>Book</th><th>Trades</th><th>WR</th><th>P&amp;L</th><th>Avg</th>
      <th>Best day</th><th>Worst day</th>
    </tr></thead>
    <tbody>${rows.map(r => {
      if (!r.n) return `<tr><td>${r.book}</td><td>0</td><td colspan="5" class="muted-text">no closed trades</td></tr>`;
      return `<tr>
        <td>${['Day Trader','Wave Rider','Contrarian','Clockwork'].includes(r.book)
              ? engBadge(r.book) : `<strong>${r.book}</strong>`}</td>
        <td>${r.n}</td>
        <td>${r.wr}%</td>
        <td>${money(r.pnl)}</td>
        <td>${money(r.avg)}</td>
        <td>${money(r.best.pnl)} <small class="muted-text">${day(r.best.date)}</small></td>
        <td>${money(r.worst.pnl)} <small class="muted-text">${day(r.worst.date)}</small></td>
      </tr>`;
    }).join('')}</tbody>
  </table>`;
}

// ── Mode badge ─────────────────────────────────────────────
function renderModeBadge(mode) {
  const badge  = document.getElementById('mode-badge');
  const header = document.getElementById('app-header');
  const isLive = mode && mode !== 'paper' && mode !== 'UNKNOWN';
  badge.textContent = isLive ? 'LIVE' : 'PAPER';
  badge.className   = 'mode-badge' + (isLive ? ' live' : '');
  header.className  = 'header ' + (isLive ? 'live-mode' : 'paper-mode');
}

// ── Services ───────────────────────────────────────────────
// Prose destined for a title="" attribute. These strings contain quotes, apostrophes
// and angle brackets; interpolating them raw silently truncates the tooltip at the
// first quote (or worse).
// Signed money, always. A bare "$59.35" in red is ambiguous; "-$59.35" is not.
function money(v) {
  const n = Number(v || 0);
  return `${n < 0 ? '-' : '+'}$${Math.abs(n).toFixed(2)}`;
}

function attrEsc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/"/g, '&quot;')
    .replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function renderServices(svcs) {
  const row = document.getElementById('services-row');
  if (!svcs) { row.innerHTML = ''; return; }
  // Sep 22 2026: these pills used to be a boolean "is the plist loaded", which is
  // true for a crashed daemon and for a scheduled job that is correctly idle. They
  // could not report a failure. Now four states, and `idle` is styled apart from
  // `up` so a dormant scheduled job never reads as a live process.
  // Sep 22 2026: one ROW per role instead of 22 pills on one wrapping line —
  // Brokers / Books / Data / Instruments / Watchdogs each get their own line and
  // their own colour, so "which layer is broken" is a glance, not a search.
  // Scheduled jobs show when they last RAN: a bare grey "idle" chip reads as
  // "off", which is alarming for a trading engine that is simply between runs.
  const groups = Array.isArray(svcs) ? svcs
    : [{ role: '', items: Object.entries(svcs).map(([name, v]) =>
        (v && typeof v === 'object') ? { ...v, name } : { name, state: v ? 'up' : 'down' }) }];
  row.innerHTML = groups.map(g => {
    const bad = (g.items || []).filter(i => !i.ok).length;
    const pills = (g.items || []).map(st => {
      const flag = st.state === 'down' ? ' ✕'
                 : st.state === 'failing' ? ' !'
                 : st.state === 'stale' ? ' ⏱' : '';
      const ago = (st.state === 'idle' && st.ago)
        ? `<span class="svc-ago">${st.ago}</span>` : '';
      // Purpose FIRST, run-state second. The pill already shows how it is doing;
      // the question these rows kept raising is what the thing actually IS — turbo,
      // ref_prices and heartbeat especially. Escaped, because these sentences contain
      // quotes and apostrophes that would otherwise break out of the title attribute.
      const tip = attrEsc(
        (st.what ? st.what + '\n\n' : '') + (st.detail || st.state));
      return `<div class="svc-pill ${st.state}" title="${tip}">
         <span class="dot"></span>${st.name}${flag}${ago}
       </div>`;
    }).join('');
    const roleTip = attrEsc(
      (g.what ? g.what + '\n\n' : '') +
      `${(g.items||[]).length} services, ${bad} needing attention`);
    return `<div class="svc-line" data-role="${g.role}">
        <span class="svc-role" title="${roleTip}">${g.role}</span>
        <span class="svc-pills">${pills}</span>
      </div>`;
  }).join('');
}


// ── Regime chip ────────────────────────────────────────────
function renderRegime(regime) {
  const chip = document.getElementById('regime-chip');
  if (!regime) return;
  const lbl = regime.label || 'UNKNOWN';
  chip.textContent  = `REGIME: ${lbl}`;
  chip.className    = `regime-chip ${lbl}`;
}

// ── Session P&L bar ────────────────────────────────────────
function renderSessionPnl(eq, opt, futIbkr, futTc) {
  const bar = document.getElementById('session-pnl-bar');
  const fmt = (v, label) => {
    if (v === undefined || v === null) return '';
    const cls = v > 0 ? 'pnl-pos' : (v < 0 ? 'pnl-neg' : '');
    const sign = v >= 0 ? '+' : '';
    return `<span class="spnl-item"><span class="spnl-label">${label}</span><span class="${cls}">${sign}$${Math.abs(v).toFixed(0)}</span></span>`;
  };
  bar.innerHTML = fmt(eq?.pnl, 'EQ') + fmt(opt?.pnl, 'OPT') + fmt(futIbkr?.pnl, 'FUT-IBKR') + fmt(futTc?.pnl, 'FUT-TC');
}

// ── Summary cards ──────────────────────────────────────────
function renderSummaryCards(eq, opt, futIbkr, futTc) {
  const pnlClass = v => v > 0 ? 'pnl-pos' : (v < 0 ? 'pnl-neg' : 'pnl-zero');
  const sign = v => v >= 0 ? '+' : '';
  const fmt = v => v != null ? `<span class="${pnlClass(v)}">${sign(v)}$${Math.abs(v).toFixed(2)}</span>` : '<span class="pnl-zero">—</span>';

  // Equity — the TOTAL across all four equity books (Day Trader, Wave Rider,
  // Contrarian, Clockwork). Until Sep 22 2026 this read the Day Trader table
  // alone and silently dropped the other three; `eq.books` now carries the
  // composition so a missing book is visible on the card instead of hidden.
  document.getElementById('eq-pnl').innerHTML = fmt(eq?.pnl);
  const eqSub = document.getElementById('eq-sub');
  eqSub.textContent =
    `${eq?.open ?? 0} open  ·  ${eq?.trades ?? 0} closed today` +
    (eq?.wr != null ? `  ·  ${eq.wr}% WR` : '');
  if (eq?.books?.length) {
    const active = eq.books.filter(b => b.trades > 0 || b.open > 0);
    eqSub.title = 'All equity books:\n' + eq.books.map(b =>
      `${b.name}: ${b.pnl >= 0 ? '+' : '-'}$${Math.abs(b.pnl).toFixed(2)}` +
      `  (${b.trades} closed, ${b.open} open)`).join('\n');
    // Name the books that actually moved the number, so a single-book day is never
    // mistaken for the whole equity side — and make each one a LINK into the activity
    // feed filtered to that book. Reading "Clockwork -$180" and then having to hunt
    // for the legs behind it was the gap; the number now takes you to them.
    const movers = eq.books.filter(b => b.trades > 0);
    if (movers.length) {
      const html = movers.map(b => {
        const cls = b.pnl >= 0 ? 'pnl-pos' : 'pnl-neg';
        return `<a class="book-link" data-book="${b.name}" href="#"
                   title="Show ${b.name}'s entries and exits in the activity feed">`
             + `<span class="eng-dot" data-eng="${b.name}"></span>`
             + `${b.name.split(' ')[0]} <span class="${cls}">${money(b.pnl)}</span></a>`;
      }).join('<span class="book-sep">/</span>');
      eqSub.innerHTML = eqSub.textContent + '  ·  ' + html;
    }
    void active;
  }

  // Options
  document.getElementById('opt-pnl').innerHTML = fmt(opt?.pnl);
  document.getElementById('opt-sub').textContent =
    `${opt?.open ?? 0} open  ·  Θ ${opt?.theta != null ? opt.theta.toFixed(0) : '—'}/day`;

  // Futures — IBKR and TC kept separate (Aug 9 2026): two real accounts,
  // different prop rules, each figure already includes its own NY + London leg.
  document.getElementById('fut-ibkr-pnl').innerHTML = fmt(futIbkr?.pnl);
  document.getElementById('fut-ibkr-sub').textContent =
    `${futIbkr?.trades ?? 0} closed today` +
    (futIbkr?.wr != null ? `  ·  ${futIbkr.wr}% WR` : '');

  document.getElementById('fut-tc-pnl').innerHTML = fmt(futTc?.pnl);
  document.getElementById('fut-tc-sub').textContent =
    `${futTc?.trades ?? 0} closed today` +
    (futTc?.wr != null ? `  ·  ${futTc.wr}% WR` : '');
}

// ── Daily P&L by system — stacked bars, last 15 sessions ──
const BOOK_COLORS = {
  equity:      { fill: 'rgba(63,185,80,0.65)',  border: '#3fb950' },   // green
  options:     { fill: 'rgba(163,113,247,0.65)', border: '#a371f7' },  // purple
  futures_ibkr:{ fill: 'rgba(79,156,246,0.65)',  border: '#4f9cf6' },  // blue
  futures_tc:  { fill: 'rgba(227,179,65,0.65)',  border: '#e3b341' },  // gold
};

function renderPnlChart(history) {
  if (!history || history.length === 0) return;
  const labels = history.map(d => d.date ? d.date.slice(5) : '');
  const mk = key => history.map(d => d[key] ?? 0);
  const series = [
    { label: 'Equity',       key: 'equity'       },
    { label: 'Options',      key: 'options'      },
    { label: 'Futures IBKR', key: 'futures_ibkr' },
    { label: 'Futures TC',   key: 'futures_tc'   },
  ];

  if (pnlChart) {
    pnlChart.data.labels = labels;
    series.forEach((s, i) => { pnlChart.data.datasets[i].data = mk(s.key); });
    pnlChart.update('none');
    return;
  }

  const ctx = document.getElementById('pnl-chart').getContext('2d');
  pnlChart = new Chart(ctx, {
    type: 'bar',
    data: {
      labels,
      datasets: series.map(s => ({
        label: s.label,
        data: mk(s.key),
        backgroundColor: BOOK_COLORS[s.key].fill,
        borderColor: BOOK_COLORS[s.key].border,
        borderWidth: 1, borderRadius: 2,
        // Sep 22 2026: bars were thin enough that a small segment (a $21 equity day
        // next to $1,300 of futures) was a hairline you could not attribute to a
        // book. Wider bars give every segment enough area to read its colour.
        categoryPercentage: 0.92,
        barPercentage: 0.96,
      })),
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { display: true, position: 'top', align: 'end',
                  labels: { color: '#8b949e', boxWidth: 10, boxHeight: 10, font: { size: 10 } } },
        tooltip: {
          callbacks: {
            label: ctx => ` ${ctx.dataset.label}: ${ctx.raw >= 0 ? '+' : ''}$${ctx.raw.toFixed(2)}`,
            footer: items => {
              const row = history[items[0].dataIndex];
              return `Day total: ${row.total >= 0 ? '+' : ''}$${(row.total ?? 0).toFixed(2)}`;
            },
          },
          backgroundColor: '#21262d', borderColor: '#30363d', borderWidth: 1,
          titleColor: '#e6edf3', bodyColor: '#8b949e', footerColor: '#e6edf3',
        },
      },
      scales: {
        x: { stacked: true, grid: { display: false }, ticks: { color: '#7d8590', font: { size: 10 } } },
        y: {
          stacked: true,
          // Zero line drawn brighter + thicker so near-zero "scratch" bars
          // clearly read as above or below breakeven.
          grid: {
            color:     c => c.tick.value === 0 ? '#8b949e' : '#21262d',
            lineWidth: c => c.tick.value === 0 ? 2 : 1,
          },
          // `grace` keeps the biggest day off the frame; more ticks make small bars
          // measurable instead of just visible.
          grace: '8%',
          ticks: { color: '#7d8590', font: { size: 10 }, maxTicksLimit: 9,
                   callback: v => `$${v >= 0 ? '+' : ''}${v.toFixed(0)}` }
        }
      }
    }
  });
}

// ── Engine badge (Sep 22 2026) ────────────────────────────
// Four equity books trade the same account, so "which engine owns this row" is the
// first thing to read. One hue per engine (see style.css), used identically in the
// positions table, the ENGINES scoreboard and the Fleet-capital chips. The colour
// always travels WITH the name — it is a second channel, never the only one, so the
// table stays readable for red/green colour-vision deficiency and in print.
// "6t" was unlabelled jargon — nothing on the page said t meant trades. Spelling it
// out costs a few pixels and removes a question.
function tradeCount(n) {
  const v = n ?? 0;
  return `<span class="trade-n">${v} ${v === 1 ? 'trade' : 'trades'}</span>`;
}

function engBadge(name) {
  const n = name || 'Day Trader';
  return `<span class="eng-name" data-eng="${n}"><span class="eng-bar"></span>${n}</span>`;
}

// ── Equity table ──────────────────────────────────────────
function renderEquityTable(positions) {
  const el = document.getElementById('equity-table');
  document.getElementById('eq-count').textContent = `${positions?.length ?? 0} open`;
  if (!positions || positions.length === 0) {
    el.innerHTML = '<div class="empty-state">No open equity positions</div>';
    return;
  }
  // Sep 22 2026: leads with the BOOK, because four engines hold shares in the same
  // account and a symbol can be owned by two of them at once (VICR was). "Plan" is
  // the column that answers the question this table kept failing: when does it close?
  // Sector / setup / target moved into the row tooltip to keep it readable.
  el.innerHTML = `<table class="positions-table">
    <thead><tr>
      <th>Book</th><th>Symbol</th><th>Side</th><th>Entry</th><th>Now</th>
      <th>Unreal P&amp;L</th><th>%</th><th>Stop</th>
      <th title="when this book intends to exit">Plan</th><th>Since</th><th>Status</th>
      <th title="Asks the book that owns this position to close it now, through its own exit path.">Close</th>
    </tr></thead>
    <tbody>${positions.map(renderEquityRow).join('')}</tbody>
  </table>`;
}

function renderEquityRow(p) {
  const pnlCls = (p.unreal_pnl || 0) >= 0 ? 'pnl-pos' : 'pnl-neg';
  const pnlSign = (p.unreal_pnl || 0) >= 0 ? '+' : '';
  const pctCls  = (p.unreal_pct || 0) >= 0 ? 'pnl-pos' : 'pnl-neg';
  const since = p.entry_date ? p.entry_date.slice(5).replace('-', '/') : '—';
  const at    = p.entry_time ? ' ' + p.entry_time.slice(0, 5) : '';
  const tip   = [p.setup_type ? 'setup ' + p.setup_type : '',
                 p.sector ? 'sector ' + p.sector : '',
                 p.target_price ? 'target $' + p.target_price.toFixed(2) : '',
                 p.shares ? p.shares + ' shares' : ''].filter(Boolean).join('  ·  ');
  return `<tr title="${tip}">
    <td><small>${engBadge(p.book)}</small></td>
    <td><strong>${p.symbol}</strong></td>
    <td><span class="side-${(p.side||'').toLowerCase()}">${p.side||'—'}</span></td>
    <td>$${(p.entry_price||0).toFixed(2)}</td>
    <td>$${(p.current_price||0).toFixed(2)}</td>
    <td class="${pnlCls}">${p.unreal_pnl != null ? `${pnlSign}$${Math.abs(p.unreal_pnl).toFixed(2)}` : '—'}</td>
    <td class="${pctCls}">${p.unreal_pct != null ? `${p.unreal_pct >= 0 ? '+' : ''}${p.unreal_pct.toFixed(2)}%` : '—'}</td>
    <td>${p.stop_price ? '$'+p.stop_price.toFixed(2) : '—'}</td>
    <td><small class="muted-text">${p.exit_plan || '—'}</small></td>
    <td><small>${since}${at}</small></td>
    <td><span class="status-${(p.status||'ok').toLowerCase()}">${p.status||'OK'}</span></td>
    <td>${closeBtn(p)}</td>
  </tr>`;
}

function renderOptionsTable(positions, health) {
  const el = document.getElementById('options-table');
  document.getElementById('opt-count').textContent = `${positions?.length ?? 0} open`;

  const totalTheta = (positions||[]).reduce((s, p) => s + (p.theta_daily || 0), 0);
  const thetaChip = document.getElementById('opt-theta');
  if (thetaChip && positions && positions.length > 0) {
    thetaChip.textContent = `Θ ${totalTheta.toFixed(0)}/day`;
  } else if (thetaChip) {
    thetaChip.textContent = '';
  }

  // Book-level Greeks + concentration summary bar (Aug 4 2026 — moved here from
  // System Health so it sits next to the positions it describes; shown whenever
  // there's a fresh snapshot, whether or not positions are currently open).
  const h = health || {};
  const g = h.book_greeks;
  const summaryBar = g ? `
    <div class="opt-summary-bar">
      <span title="Net delta across every open leg, share-equivalent">Δ ${g.net_delta >= 0 ? '+' : ''}${g.net_delta}</span>
      <span title="What the whole options book loses per day if nothing moves">Θ ${g.net_theta >= 0 ? '+' : ''}$${g.net_theta}/day</span>
      <span title="Dollar sensitivity per 1-point move in implied volatility">vega ${g.net_vega >= 0 ? '+' : ''}${g.net_vega}</span>
      <span class="muted-text">as of ${g.ts}</span>
      ${(h.concentration||[]).length ? `<span class="muted-text">· ${h.concentration.map(x=>`${x.symbol} ${x.pct}%`).join(' · ')}</span>` : ''}
    </div>` : '';

  if (!positions || positions.length === 0) {
    const c = h.calcs_today || {};
    const w = h.whatif_14d || {};
    el.innerHTML = `${summaryBar}<div class="empty-state opt-empty-state">
      <div>No open options positions.</div>
      <div class="muted-text" style="margin-top:6px">
        Today: ${c.total ?? 0} candidates evaluated, ${c.enter ?? 0} passed every gate.
        Last 14d: ${w.n ?? 0} skipped suggestions scored via the Ghost Ledger
        (${(w.pnl ?? 0) >= 0 ? '+' : ''}$${w.pnl ?? 0} if we'd taken them).
      </div>
      <div class="muted-text" style="margin-top:4px">
        This is expected when the equity direction's book is unhealthy or no candidate
        clears the liquidity gate — not a sign anything's broken. See SYSTEM HEALTH above
        for which one.
      </div>
    </div>`;
    return;
  }
  el.innerHTML = `${summaryBar}<table class="positions-table">
    <thead><tr>
      <th>Symbol</th><th>Strategy</th><th>Expiry / DTE</th><th>Strikes</th>
      <th>Paid</th><th>Now</th><th>Unreal P&amp;L</th><th>%</th>
      <th>Δ</th><th>Θ/day</th><th>Earnings</th><th>Grade</th><th>Status</th>
      <th title="Asks options_trader to close this position through its own OPT CLOSE path.">Close</th>
    </tr></thead>
    <tbody>${positions.map(renderOptionsRow).join('')}</tbody>
  </table>
  <div class="muted-text" style="margin-top:6px">Tap a row for strike ladder + theta decay (coming once a position and 2+ days of chain snapshots exist).</div>`;
}

function renderOptionsRow(p) {
  const pnlCls  = (p.unreal_pnl || 0) >= 0 ? 'pnl-pos' : 'pnl-neg';
  const pctCls  = (p.pnl_pct    || 0) >= 0 ? 'pnl-pos' : 'pnl-neg';
  const sign    = v => v >= 0 ? '+' : '';

  const strikes = p.short_strike
    ? `${p.long_strike}/${p.short_strike}`
    : (p.long_strike || '—');
  const expiry = p.expiry ? p.expiry.toString().replace(/(\d{4})(\d{2})(\d{2})/, '$2/$3') : '—';
  const dte    = p.dte != null ? `${p.dte}d` : '';

  let earningsBadge = '—';
  if (p.earnings_days != null) {
    const cls = p.earnings_days <= 7 ? 'pnl-neg' : (p.earnings_days <= 14 ? 'pnl-neg' : '');
    earningsBadge = `<span class="${cls}">${p.earnings_days}d</span>`;
  }

  return `<tr>
    <td><strong>${p.symbol}</strong></td>
    <td><small>${p.strategy||'—'}</small></td>
    <td>${expiry} <span class="muted-text">${dte}</span></td>
    <td>${strikes} ${p.right||''}</td>
    <td>${p.premium_paid ? '$'+p.premium_paid.toFixed(2) : '—'}</td>
    <td>${p.current_value != null ? '$'+p.current_value.toFixed(2) : '—'}</td>
    <td class="${pnlCls}">${p.unreal_pnl != null ? `${sign(p.unreal_pnl)}$${Math.abs(p.unreal_pnl).toFixed(2)}` : '—'}</td>
    <td class="${pctCls}">${p.pnl_pct != null ? `${sign(p.pnl_pct)}${p.pnl_pct.toFixed(1)}%` : '—'}</td>
    <td>${p.delta != null ? p.delta.toFixed(2) : '—'}</td>
    <td class="pnl-neg">${p.theta_daily != null ? p.theta_daily.toFixed(0) : '—'}</td>
    <td>${earningsBadge}</td>
    <td>${p.grade ? `<span class="tag">${p.grade}</span>` : '—'}</td>
    <td><span class="status-badge ${p.status}">${p.status}</span></td>
    <td>${closeBtn(p)}</td>
  </tr>`;
}

// ── Futures table ─────────────────────────────────────────
function renderFuturesTable(positions, session) {
  const el = document.getElementById('futures-table');
  document.getElementById('fut-count').textContent = `${positions?.length ?? 0} open`;
  const chip = document.getElementById('session-chip');
  if (chip) chip.textContent = session || 'OFF';

  if (!positions || positions.length === 0) {
    const msg = session === 'LONDON'
      ? 'No London session position open'
      : (session === 'NY' ? 'No NY session position open' : 'Market closed / no active session');
    el.innerHTML = `<div class="empty-state">${msg}</div>`;
    return;
  }
  el.innerHTML = `<table class="positions-table">
    <thead><tr>
      <th>Account</th><th>Symbol</th><th>Contract</th><th>Session</th><th>Side</th>
      <th>Contracts</th><th>Entry</th><th>Now</th><th>Stop</th>
      <th title="BASE_TARGET_PTS backstop — 1500pts out. Has fired 0 times in 951 trades over 5.5yr; the best trade ever ran 378pts. It is a disaster cap and the numerator of the MIN_RR gate, NOT a level being chased.">Cap<sup>?</sup></th>
      <th>Unreal P&amp;L</th><th>Nearest real exit</th><th>Status</th><th>Crest Watch</th>
      <th title="Asks the trader that owns this position to flatten it, through _force_close_all (NY) or its own exit (London).">Close</th>
    </tr></thead>
    <tbody>${positions.map(p => {
      const pnlCls = (p.unreal_pnl || 0) >= 0 ? 'pnl-pos' : 'pnl-neg';
      const sign   = (p.unreal_pnl || 0) >= 0 ? '+' : '';
      return `<tr>
        <td><span class="account-badge ${(p.account_mode||'').toLowerCase()}">${p.account_mode||'—'}</span></td>
        <td><strong>${p.symbol||'—'}</strong></td>
        <td><small>${p.contract_month||'—'}</small></td>
        <td><small>${p.session||'—'}</small></td>
        <td><span class="side-${(p.side||'').toLowerCase()}">${p.side||'—'}</span></td>
        <td>${p.qty||0}</td>
        <td>${p.entry_price != null ? p.entry_price.toFixed(2) : '—'}</td>
        <td>${p.market_price != null ? p.market_price.toFixed(2) : '—'}</td>
        <td>${p.stop_price != null ? p.stop_price.toFixed(2) : '—'}</td>
        <td class="muted">${p.target_price != null ? p.target_price.toFixed(2) : '—'}</td>
        <td class="${pnlCls}">${p.unreal_pnl != null ? `${sign}$${Math.abs(p.unreal_pnl).toFixed(2)}` : '—'}</td>
        <td>${renderExitMap(p.exit_map)}</td>
        <td><span class="status-badge ${p.status||'OK'}">${p.status||'OK'}</span></td>
        <td>${renderCrestBadge(p.crest_watch)}</td>
        <td>${closeBtn(p)}</td>
      </tr>`;
    }).join('')}</tbody>
  </table>`;
}

function renderExitMap(em) {
  // The "Cap" column is the 1500pt backstop and is meaningless day to day.
  // THIS column answers the question that actually matters when you are deciding
  // whether to close by hand: how far is this position from an exit that can
  // really fire? Nearest first; hover for the full list.
  if (!em)          return `<span class="exit-badge none" title="Trader has not published an exit map for this position yet">—</span>`;
  if (em.stale)     return `<span class="exit-badge stale" title="Exit map is ${em.age_s}s old — the trader may not be running">stale ${Math.round(em.age_s/60)}m</span>`;
  if (!em.exits || !em.exits.length) return `<span class="exit-badge none">—</span>`;
  const first = em.exits[0];
  // "stop 29,478.75    40.2pt (0.14%)  → locks $+203"
  const m = first.match(/^(\S+)\s+([\d,\.]+)?\s*([\d\.]+)pt \(([\d\.]+)%\)/);
  const title = em.exits.join('\n').replace(/"/g, '&quot;');
  if (!m) return `<span class="exit-badge info" title="${title}">${first.split('→')[0].trim()}</span>`;
  const pts = parseFloat(m[3]);
  const tier = pts <= 25 ? 'close' : pts <= 60 ? 'near' : 'far';
  return `<span class="exit-badge ${tier}" title="${title}">${m[1]} ${m[3]}pt (${m[4]}%)</span>`;
}

function renderCrestBadge(cw) {
  if (!cw || cw.risk_score == null) {
    return `<span class="crest-badge none" title="No check yet — fires once a position peaks 100+pts profitable">not yet checked</span>`;
  }
  const tier = cw.risk_score >= 55 ? 'high' : 'low';
  const streakNote = cw.streak > 1 ? ` ×${cw.streak}` : '';
  const time = cw.checked_at ? cw.checked_at.slice(11, 16) : '';
  const title = `${cw.reasoning || ''}${time ? ` (as of ${time})` : ''}`.replace(/"/g, '&quot;');
  return `<span class="crest-badge ${tier}" title="${title}">risk ${cw.risk_score}${streakNote}</span>`;
}

// ── Sector grades ─────────────────────────────────────────
function renderSectors(sectors) {
  const el = document.getElementById('sector-grid');
  if (!sectors || sectors.length === 0) { el.innerHTML = ''; return; }
  el.innerHTML = sectors.map(s => {
    const wr = s.wr_30d != null ? ` ${(s.wr_30d * 100).toFixed(0)}% WR` : '';
    const n  = s.trade_count ? ` (${s.trade_count}t)` : '';
    const title = `${s.sector}${wr}${n} — refreshed nightly at 23:00 ET from the trailing `
                + `30 days of closed trades (min 5 trades/sector), NOT intraday. This grade `
                + `has been fixed since last night's run regardless of what's happened today.`;
    return `<div class="sector-pill ${s.grade||'NEUTRAL'}" title="${title}">
      <span class="sname">${s.sector}</span>
      <span class="sgrade">${s.grade||'—'}</span>
    </div>`;
  }).join('');
}

// ── Alerts ────────────────────────────────────────────────
function renderAlerts(alerts) {
  const el   = document.getElementById('alerts-list');
  const chip = document.getElementById('alert-count');
  const high = (alerts||[]).filter(a => a.level === 'HIGH').length;
  if (chip) {
    chip.textContent = high > 0 ? `${high} HIGH` : `${(alerts||[]).length}`;
    chip.style.display = (alerts||[]).length === 0 ? 'none' : '';
  }
  if (!alerts || alerts.length === 0) {
    el.innerHTML = '<div class="empty-state">No active alerts</div>';
    return;
  }
  el.innerHTML = alerts.map(a =>
    `<div class="alert-row">
       <span class="alert-level ${a.level}">${a.level}</span>
       <span class="alert-sym">${a.symbol}</span>
       <span class="alert-msg">${a.message}</span>
       <span class="alert-time">${a.time||''}</span>
     </div>`
  ).join('');
}

// ── Calendar ──────────────────────────────────────────────
function renderCalendar(earnings, macro) {
  const earEl = document.getElementById('earnings-cal');
  const macEl = document.getElementById('macro-cal');

  if (!earnings || earnings.length === 0) {
    earEl.innerHTML = '<div class="empty-state">No earnings for open positions in next 30 days</div>';
  } else {
    earEl.innerHTML = earnings.map(e =>
      `<div class="cal-row cal-urgency-${e.urgency}" onclick="toggleCalDetail(this)">
         <span class="cal-date">${e.date}</span>
         <span class="cal-sym">${e.symbol}</span>
         <span class="cal-msg">Earnings · ${e.verticals}</span>
         <span class="cal-days">${e.days_to}d away</span>
       </div>
       <div class="cal-detail" style="display:none">
         Positions exposed: ${e.verticals} ·
         <a class="cal-link" href="https://finance.yahoo.com/quote/${e.symbol}/financials/" target="_blank">View on Yahoo Finance ↗</a>
       </div>`
    ).join('');
  }

  if (!macro || macro.length === 0) {
    macEl.innerHTML = '<div class="empty-state">No macro events in next 30 days</div>';
  } else {
    macEl.innerHTML = macro.map(m => {
      const link = m.link ? `<a class="cal-link" href="${m.link}" target="_blank">Source ↗</a>` : '';
      return `<div class="cal-row" onclick="toggleCalDetail(this)">
         <span class="cal-date">${m.date}</span>
         <span class="cal-cat ${m.category}">${m.category}</span>
         <span class="cal-msg">${m.event}</span>
         <span class="cal-days">${m.days_to}d</span>
       </div>
       <div class="cal-detail" style="display:none">${link}</div>`;
    }).join('');
  }
}

function toggleCalDetail(row) {
  const detail = row.nextElementSibling;
  if (detail && detail.classList.contains('cal-detail')) {
    detail.style.display = detail.style.display === 'none' ? '' : 'none';
  }
}

// ── Activity feed ─────────────────────────────────────────
function actMatches(a) {
  const f = actFilter;
  if (f.ev !== 'ALL' && a.ev !== f.ev) return false;
  if (f.book !== 'ALL') {
    const grp = BOOK_GROUPS[f.book];
    if (grp ? !grp.includes(a.book) : a.book !== f.book) return false;
  }
  if (f.days) {
    // Calendar days back from today, counted on the row's own date. "Today" is 1.
    const d = new Date(); d.setHours(0, 0, 0, 0);
    d.setDate(d.getDate() - (f.days - 1));
    const iso = d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0')
              + '-' + String(d.getDate()).padStart(2, '0');
    if ((a.dt || '') < iso) return false;
  }
  return true;
}

function renderActivityFeed(activities) {
  const el = document.getElementById('activity-feed');
  const sum = document.getElementById('activity-summary');
  const filtered = (activities || []).filter(actMatches);

  // What the filter actually selected, and what it is worth. Without this the feed
  // answers "which legs" but not "so what did that book do", which is the reason to
  // drill into one engine in the first place.
  if (sum) {
    const exits = filtered.filter(a => a.ev === 'EXIT' && a.pnl != null);
    const net   = exits.reduce((s, a) => s + a.pnl, 0);
    const wins  = exits.filter(a => a.pnl > 0).length;
    const bits  = [`${filtered.length} of ${(activities || []).length} legs`];
    if (exits.length) {
      const cls = net >= 0 ? 'pnl-pos' : 'pnl-neg';
      bits.push(`${exits.length} closed`);
      bits.push(`net <span class="${cls}">${money(net)}</span>`);
      bits.push(`${Math.round(wins / exits.length * 100)}% win`);
    }
    sum.innerHTML = bits.join('  ·  ');
  }

  if (!filtered.length) {
    el.innerHTML = '<div class="empty-state">Nothing matches these filters</div>';
    return;
  }

  el.innerHTML = filtered.map(a => {
    const evTag = `<span class="act-ev ${a.ev}">${a.ev}</span>`;
    const ts    = `${a.dt || ''} ${(a.tm || '').slice(0, 5)}`;
    // The BOOK, on every row including exits. Until Sep 24 2026 exit rows showed the
    // reason alone, so "which engine sold this" was unanswerable from the feed — and
    // Clockwork, whose reason was also blank, showed nothing at all.
    const bookTag = a.book
      ? `<span class="act-book"><span class="eng-dot" data-eng="${a.book}"></span>${a.book}</span>`
      : `<span class="tag ${(a.vert || '').toLowerCase()}">${a.vert || '—'}</span>`;
    const acct = a.account ? ` <span class="account-badge ${String(a.account).toLowerCase()}">${a.account}</span>` : '';

    const reconciledTag = a.setup === 'RECONCILED'
      ? `<span class="tag reconciled" title="Not a strategy decision — broker-side correction, excluded from P&L/WR stats">🔧 RECONCILED</span> `
      : '';

    let desc = '';
    if (a.ev === 'ENTRY') {
      desc = `<span class="side-${(a.side || 'long').toLowerCase()}">${a.side || ''}</span> `;
      if (a.price) desc += `@ $${Number(a.price).toFixed(2)} · `;
      const meta = [a.setup, a.sector].filter(Boolean).join(' · ');
      desc += `<small>${meta || '—'}</small>`;
    } else {
      desc = reconciledTag + `<small>${a.reason || '<em class="muted-text">no exit reason recorded</em>'}</small>`;
    }

    // Negative P&L used to render as "$59.35" in red with NO minus sign, because the
    // prefix was '' for negatives and the value was Math.abs(). Colour alone carried
    // the sign, so a loss read as a gain at a glance. The sign is explicit now.
    const pnlHtml = a.pnl != null
      ? `<span class="act-pnl ${a.pnl >= 0 ? 'pnl-pos' : 'pnl-neg'}">${money(a.pnl)}</span>`
      : '';

    return `<div class="activity-row">
      <span class="act-time">${ts}</span>
      ${evTag}
      ${bookTag}${acct}
      <span class="act-sym">${a.symbol || '—'}</span>
      <span class="act-desc">${desc}</span>
      ${pnlHtml}
    </div>`;
  }).join('');
}

// ── Production checklist ──────────────────────────────────
function renderProdChecklist(items) {
  const el = document.getElementById('prod-checklist');
  if (!items || !el) return;
  el.innerHTML = items.map(item =>
    `<div class="checklist-item ${item.done ? 'done' : 'pending'}">
       <span class="check-icon">${item.done ? '✅' : '⬜'}</span>
       <span>${item.item}</span>
     </div>`
  ).join('');
}

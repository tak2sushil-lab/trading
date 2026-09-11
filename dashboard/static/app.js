/* TriVega Dashboard — auto-refresh + render logic */

let pnlChart = null;
let currentTab = 'paper';
let allActivity = [];
let currentFilter = 'ALL';
let calView = 'earnings';

// ── Bootstrap ─────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  loadData();
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

// ── Activity filter ────────────────────────────────────────
function filterActivity(vert) {
  currentFilter = vert;
  document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
  event.target.classList.add('active');
  renderActivityFeed(allActivity);
}

// ── Master render ──────────────────────────────────────────
function renderAll(d) {
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
      <th>engine</th><th>open</th><th>today</th><th>7-day</th><th>win</th>
      <th title="exits the engine made itself vs forced by reconcile/manual">own exits</th>
      <th>last</th></tr></thead><tbody>` +
    rows.map(r => `<tr title="${r.desc || ''}">
      <td><b>${r.engine}</b>${r.err ? ' <span class="neg" title="' + r.err + '">!</span>' : ''}</td>
      <td>${r.open}</td>
      <td>${r.today_n}t ${money(r.today_pnl)}</td>
      <td>${r.wk_n}t ${money(r.wk_pnl)}</td>
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

  const bookChip = (name, b) => {
    if (!b) return '';
    const cls = b.state === 'ON' ? 'pos' : (b.state === 'OFF' ? 'neg' : '');
    const drift = b.drift == null ? '' : ` ${b.drift > 0 ? '+' : ''}${b.drift}%/sig`;
    return `<span class="health-chip ${cls}" title="${b.desc || ''}"><b>${name}</b> ${b.state}${drift}</span>`;
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
      <span title="Shadow paper result — 1 MNQ contract equivalent, no real orders">${s.n ?? 0} shadow trades · ${(s.pts_total ?? 0) >= 0 ? '+' : ''}${s.pts_total ?? 0} pts all-time · ${(s.pts_14d ?? 0) >= 0 ? '+' : ''}${s.pts_14d ?? 0} pts last 14d</span>
    </div>
    ${renderOptionsHealth(h.options)}
    ${renderFieldReport(h.field_report)}`;
}

// ── Fish Finder row inside SYSTEM HEALTH (Aug 8 2026 redesign) ──────────
// Replaces the original single-template Regime-Adaptive Suite. Gate states
// come from fishfinder_gate_log (written live by auto_trader.py) — NOT an
// in-memory read, since the dashboard is a separate process from autotrader.
function renderFishFinderHealth(ff) {
  if (!ff) return '';
  const cg = ff.crowd_gauge || {};
  const bc = ff.bite_check || {};
  const cgCls = cg.state === 'BLOCKING' ? 'neg' : (cg.state === 'OK' ? 'pos' : '');
  const bcCls = bc.state === 'OFF' ? 'neg' : (bc.state === 'ON' ? 'pos' : '');
  const f = ff.funnel || {};
  const TEMPLATES = [
    ['FISHFINDER_ADX_TREND', 'ADX Trend'],
    ['FISHFINDER_KELTNER_REVERT', 'Keltner Revert'],
    ['FISHFINDER_RSI_REVERT', 'RSI Revert'],
  ];
  const money = v => `${v >= 0 ? '+' : '−'}$${Math.abs(v).toFixed(0)}`;
  const funnelStr = bucket => {
    if (!bucket) return '—';
    const parts = TEMPLATES.map(([key, name]) => {
      const x = bucket[key] || { n: 0, pnl: 0 };
      return `${name} ${x.n}t ${money(x.pnl)}`;
    });
    return parts.join(' · ');
  };
  return `
    <div class="health-row">
      <span class="health-label" title="Fish Finder: tests every symbol against all 3 templates (ADX Trend / Keltner Revert / RSI Revert) on its own signals every scan, instead of one market regime picking a single template for all 241 symbols. Replaced the original single-template design Aug 8 2026 — see GLOSSARY.md.">Fish Finder</span>
      <span class="health-chip ${cgCls}" title="Crowd Gauge: blocks trend-calls when the ~60-symbol universe sample's correlation exceeds 0.35 (herd/panic move, not a real idiosyncratic trend). Daily-frequency, cached once/day.">Crowd Gauge ${cg.corr != null ? cg.corr : '—'} / ${cg.max ?? 0.35}</span>
      <span class="health-chip ${bcCls}" title="Bite Check: pauses new WEAK-regime ADX Trend entries if the trailing 7 trading days of those trades have net-lost money. Only evaluated on WEAK-regime scans — shows N/A otherwise.">Bite Check ${bc.state || '—'}</span>
    </div>
    <div class="health-row">
      <span class="health-label" title="Per-template trade count and P&amp;L split, today / trailing 7d / trailing 30d">Fish Finder P&amp;L</span>
      <span class="health-detail-inline">today: ${funnelStr(f.today)}</span>
    </div>
    <div class="health-row">
      <span class="health-label"></span>
      <span class="health-detail-inline">7d: ${funnelStr(f['7d'])}</span>
    </div>
    <div class="health-row">
      <span class="health-label"></span>
      <span class="health-detail-inline">30d: ${funnelStr(f['30d'])}</span>
    </div>`;
}

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
  // Book-level Greeks + concentration moved to the Options Positions card
  // (Aug 4 2026) — they describe the positions, so they live next to them.
  // This row stays focused on the gating/funnel story specifically.
  return `
    <div class="health-row">
      <span class="health-label" title="Options trade only in directions whose equity book is healthy (same Books row above). Funnel = calculator runs today; Ghost Ledger = what the suggestions we did NOT take would have made (scored nightly). Full Greeks + concentration are in the Options Positions card below.">Options</span>
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
        <td><strong>${r.book}</strong></td>
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
function renderServices(svcs) {
  const row = document.getElementById('services-row');
  if (!svcs) { row.innerHTML = ''; return; }
  row.innerHTML = Object.entries(svcs).map(([name, up]) =>
    `<div class="svc-pill ${up ? 'up' : 'down'}">
       <span class="dot"></span>${name}
     </div>`
  ).join('');
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

  // Equity
  document.getElementById('eq-pnl').innerHTML = fmt(eq?.pnl);
  document.getElementById('eq-sub').textContent =
    `${eq?.open ?? 0} open  ·  ${eq?.trades ?? 0} closed today` +
    (eq?.wr != null ? `  ·  ${eq.wr}% WR` : '');

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
          ticks: { color: '#7d8590', font: { size: 10 },
                   callback: v => `$${v >= 0 ? '+' : ''}${v.toFixed(0)}` }
        }
      }
    }
  });
}

// ── Equity table ──────────────────────────────────────────
function renderEquityTable(positions) {
  const el = document.getElementById('equity-table');
  document.getElementById('eq-count').textContent = `${positions?.length ?? 0} open`;
  if (!positions || positions.length === 0) {
    el.innerHTML = '<div class="empty-state">No open equity positions</div>';
    return;
  }
  el.innerHTML = `<table class="positions-table">
    <thead><tr>
      <th>Symbol</th><th>Side</th><th>Entry</th><th>Now</th>
      <th>Unreal P&amp;L</th><th>%</th><th>Stop</th><th>Target</th>
      <th>Sector</th><th>Setup</th><th>Since</th><th>Status</th>
    </tr></thead>
    <tbody>${positions.map(renderEquityRow).join('')}</tbody>
  </table>`;
}

function renderEquityRow(p) {
  const pnlCls = (p.unreal_pnl || 0) >= 0 ? 'pnl-pos' : 'pnl-neg';
  const pnlSign = (p.unreal_pnl || 0) >= 0 ? '+' : '';
  const pctCls  = (p.unreal_pct || 0) >= 0 ? 'pnl-pos' : 'pnl-neg';
  const since = p.entry_time ? p.entry_time.slice(0, 5) : '—';
  return `<tr>
    <td><strong>${p.symbol}</strong></td>
    <td><span class="side-${(p.side||'').toLowerCase()}">${p.side||'—'}</span></td>
    <td>$${(p.entry_price||0).toFixed(2)}</td>
    <td>$${(p.current_price||0).toFixed(2)}</td>
    <td class="${pnlCls}">${p.unreal_pnl != null ? `${pnlSign}$${Math.abs(p.unreal_pnl).toFixed(2)}` : '—'}</td>
    <td class="${pctCls}">${p.unreal_pct != null ? `${p.unreal_pct >= 0 ? '+' : ''}${p.unreal_pct.toFixed(2)}%` : '—'}</td>
    <td>${p.stop_price ? '$'+p.stop_price.toFixed(2) : '—'}</td>
    <td>${p.target_price ? '$'+p.target_price.toFixed(2) : '—'}</td>
    <td><small>${p.sector||'—'}</small></td>
    <td><small>${p.setup_type||'—'}</small></td>
    <td><small>${p.entry_date||''} ${since}</small></td>
    <td><span class="status-badge ${p.status}">${p.status}</span></td>
  </tr>`;
}

// ── Options table ──────────────────────────────────────────
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
function renderActivityFeed(activities) {
  const el = document.getElementById('activity-feed');
  const filtered = currentFilter === 'ALL'
    ? activities
    : activities.filter(a => a.vert === currentFilter);

  if (!filtered || filtered.length === 0) {
    el.innerHTML = '<div class="empty-state">No recent activity</div>';
    return;
  }

  el.innerHTML = filtered.map(a => {
    const vertTag = `<span class="tag ${(a.vert||'').toLowerCase()}">${a.vert||'—'}</span>`;
    const evTag   = `<span class="act-ev ${a.ev}">${a.ev}</span>`;
    const ts      = `${a.dt||''} ${(a.tm||'').slice(0,5)}`;

    const reconciledTag = a.setup === 'RECONCILED'
      ? `<span class="tag reconciled" title="Not a strategy decision — broker-side correction, excluded from P&L/WR stats">🔧 RECONCILED</span> `
      : '';

    let desc = '';
    if (a.ev === 'ENTRY') {
      desc = `<span class="side-${(a.side||'long').toLowerCase()}">${a.side||''}</span> `;
      if (a.price) desc += `@ $${Number(a.price).toFixed(2)} · `;
      desc += `<small>${a.setup||''} ${a.sector ? '· '+a.sector : ''}</small>`;
    } else {
      desc = reconciledTag + (a.reason ? `<small>${a.reason}</small>` : '');
    }

    const pnlHtml = a.pnl != null
      ? `<span class="act-pnl ${a.pnl >= 0 ? 'pnl-pos' : 'pnl-neg'}">${a.pnl >= 0 ? '+' : ''}$${Math.abs(a.pnl).toFixed(2)}</span>`
      : '';

    return `<div class="activity-row">
      <span class="act-time">${ts}</span>
      ${evTag}
      ${vertTag}
      <span class="act-sym">${a.symbol||'—'}</span>
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

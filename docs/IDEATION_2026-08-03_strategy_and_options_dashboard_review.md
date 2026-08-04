# Ideation — Strategy Validation + Options Dashboard (two YouTube reviews)

**Status: IDEATION ONLY.** Nothing here is validated against our own data, nothing is scheduled,
nothing should be built without a backtest first (Standing Rule 2). This is a captured set of
ideas to pull from when options work resumes, not a plan.

**Source:** user watched two videos on Aug 3 2026 — (1) a "Strategy Validation Terminal" that
backtests ~10 strategy archetypes across a 30-asset universe (mega-caps + sector ETFs) over 15
years and ranks survivors by OOS Sharpe; (2) a 4-layer options monitoring dashboard (Data/Valuation
→ Portfolio Analytics → Macro Gate + Claude News → Alerts/Dashboard), explicitly NOT an
auto-trader — a Bloomberg-terminal-style read-only monitor, paired with a "Claude Portfolio"
workflow (Claude Web UI does strategy research / name screening / contract selection, Claude Code
only builds the monitoring tool).

---

## What Video 1 actually showed

Tested ~10 strategy families (RSI Revert, Keltner Revert, Money Flow Index, Zscore Revert,
Ultimate Osc, Turtle, ADX Trend, Dual Momentum, Triple Screen, ...) against the *same* 30-asset
universe, then reported **mean OOS Sharpe by category**:

| Category | Mean OOS Sharpe | % survived |
|---|---|---|
| Mean Reversion | **+0.02** | 8% |
| Trend | -0.04 | 3% |
| Volume | -0.05 | 6% |
| Composite | -0.08 | 4% |
| Volatility | -0.09 | 4% |
| Pattern | -0.19 | 2% |

Mean reversion was the *only* category with a positive mean OOS Sharpe, tested across a diverse,
liquid universe (mega-caps + sector ETFs). Top individual survivors were a mix — RSI Revert on
GOOGL/XLU, but also Turtle (trend) on AAPL and Dual Momentum on NVDA — so this isn't "mean
reversion always wins," it's "mean reversion is the only *family* that reliably clears the bar
across many assets," while trend/momentum survivors are much more asset-specific and fragile.

## What Video 2 actually showed

A 4-layer options monitor, explicitly scoped as **read-only** ("the whole system stays a
monitor... nothing recommends trades or routes orders"):

1. **Data & Valuation** — pulls chain + spot, computes Greeks locally (Black-Scholes, no
   external lib), saves a dated snapshot every run so later layers can diff "what's new."
2. **Portfolio Analytics** — allocation by ticker/sector with concentration caps (40%/60%,
   informational only), aggregate book-level Greeks (net delta in share-equivalent, daily theta
   bleed in $, net vega per 1 IV point), IV rank built from its *own* accumulated snapshots (not
   a third-party feed), DTE-approaching flags that "surface the fact, do not advise rolling."
3. **Macro Gate + Claude News** — a deterministic 0-100 macro score (VIX level + 1yr percentile,
   VIX term structure/contango, breadth vs 200MA, HYG-TLT credit spread z-score — same data in,
   same score out, no LLM in this part) *plus* a daily Claude read of each held name's headlines
   (summary/sentiment/drivers/position-specific flag), cached per day, explicitly **not a trade
   trigger**.
4. **Alerts & Dashboard** — new-strike/new-expiry detection (diffs today's snapshot vs
   yesterday's), condition alerts always phrased as facts ("NOK calls hit your target level"),
   severity-tiered (HIGH/WARN/INFO) with an early "target_near" (80%-there) INFO tier before the
   HIGH alert fires, a Bloomberg-style single page (status strip, alerts, positions grid with
   progress-to-target/stop bars, theta-decay charts, a strike ladder ±6 strikes around each held
   position with held-strike highlighted and NEW badges), optional Telegram/iMessage push.

Separately, one more screenshot showed a *different* build: a regime-detection engine using
`hmmlearn.GaussianHMM` (7 states, trained on returns/range/volume-volatility, auto-identifies the
bull-run and bear-crash states) gating an 8-confirmation entry vote — a statistically-fit
alternative to hand-tuned regime thresholds.

---

## Joining the dots to TriVega

### What this *validates* — you already independently arrived at some of this

- **"News informs, doesn't trigger"** — Video 2's Layer 3 news treatment (summarize + flag,
  never buy/sell) is *exactly* the posture the Jul 18 options redesign already landed on after
  the audit found HIGH-BULL conviction was a fade signal. The news queue → Ghost Ledger change
  already did this. The video is confirmation the direction was right, not a new idea — the gap
  is presentation (see below), not philosophy.
- **A deterministic macro/context layer, separate from trade logic** — `market_context.py`
  ("Field Report," built Jul 19) is structurally the same idea as Video 2's macro gate: a
  mechanical layer + an LLM layer, log-only, explicitly not wired into any trader yet. You built
  this before seeing the video. It's just scoped system-wide (equity+futures) rather than framed
  around what an options book specifically needs, and its mechanical layer doesn't currently
  include VIX term structure or a credit-spread z-score — both cheap, deterministic additions if
  Field Report ever gets a second look.
- **Liquidity as the deciding gate, not a 5-factor score** — Video 2 doesn't show a scoring
  system at all; TriVega's own Jul 18 audit reached almost the same place independently (5-gate
  score + MC EV demoted to instrumentation, liquidity decides). The Aug 3 finding that the
  liquidity gate itself is mis-tuned (near-100% fail rate, far-OTM legs mathematically can't
  clear 15%) is a tuning problem within a direction that's already correct, not evidence the
  direction was wrong.

### What this *surfaces as a real, evidenced gap*

**1. No mean-reversion book, anywhere — but you already have the seed of one.**
Every TriVega entry strategy (equity and futures) is momentum/breakout-shaped: ORB, VWAP
reclaim, HOD break, bull flag, momentum ≥5%. There is no RSI/Keltner/Zscore-style reversion
*entry* strategy running live, on either side. Video 1's finding — mean reversion is the only
family with positive OOS Sharpe across a diverse asset set — is a specific, testable claim
against exactly the kind of systematic multi-strategy-times-multi-asset sweep TriVega hasn't run
before (`dna_analysis.py`/`find_candidates.py` screen *assets*, not *strategy families*).
But: the **Mirror Book** (fade decoder LONG-signal episodes, +505pts/75 shadow trades, on track
for its Aug 17 review) *is already a mean-reversion structure* — fading an initial signal is a
reversion bet by construction. It's just never been framed or discussed as "our mean-reversion
complement to the momentum books." Worth reframing it that way and asking whether its 30-day
graduation criteria should explicitly compare against a momentum-book baseline on the *same*
choppy days, not just against zero.

**2. The chop/trend-separation problem has been attacked from one angle only.**
Two separate, already-documented dead ends exist for this exact problem: entry-side "IB range
alone can't cleanly separate real trend from wide whipsaw" (Jul 8 exit-stack gap) and "chop-aware
exit switching remains an open problem... four different live-computable chop detectors... none
worked" (Jul 7). Every attempt so far has been a hand-tuned threshold on a single feature (RVOL,
ADX, VWAP-crossing count, IB range). The HMM screenshot is a categorically different technique —
a *learned*, multi-feature statistical regime label — that has never been tried against this
specific, twice-failed problem. Not a guarantee it works better; genuinely untested here.

**3. Options monitoring depth vs. options trading depth are two different problems, and only
one of them is broken.** The Aug 3 diagnosis (liquidity gate near-unpassable) is about the
*trading* logic. Video 2 is entirely about *monitoring* — and TriVega's options monitoring is
thin regardless of whether the trading logic works: no portfolio-level aggregate Greeks
(net delta/theta/vega across the book), no concentration caps, no new-strike/new-expiry
detection, no theta-decay projection, no strike ladder, no per-position progress-to-target/stop
visualization, no severity-tiered single alert feed with an early "getting close" tier before the
hard alert. These are independent of the entry-gate problem and could be built regardless of how
that gets resolved — a silent trading book can still be a well-instrumented one.

**4. The "Claude Portfolio" human-in-the-loop framing is worth naming, not necessarily adopting.**
Video 2's workflow keeps judgment-heavy steps (regime read, name screening, contract selection)
in Claude Web UI as a human-directed conversation, and only automates the mechanical monitoring
layer. TriVega's whole thesis has been the opposite — full automation with statistically-gated
entries. Given the Jul 18 audit found TriVega's *own* automated verdict system anti-predictive
(ENTER worse than SKIP), there's an uncomfortable parallel worth sitting with: the video's
implicit bet — that structured human judgment beats an automated score for options specifically —
rhymes with what your own data already showed. Not a recommendation to hand options entries back
to manual judgment; a note that the question is live, not settled.

---

## Idea backlog (ideation only — needs its own backtest before any of this gets built)

**Strategy research**
- Build a "strategy validation terminal" of our own: sweep a handful of reversion archetypes
  (RSI, Keltner, Zscore) and the existing momentum archetypes across the *equity universe*
  (or a liquid subset of it), by OOS Sharpe, category vs category — same methodology as Video 1,
  our data. Tells us whether the mean-reversion finding replicates here before touching Mirror
  Book or building anything new.
- If it does replicate: explore gating a reversion entry book specifically to CHOPPY/QUIET
  regime days (the regime label already exists) as a complement to the momentum books on
  STRONG/NORMAL days — same shape as the LONG/SHORT book-health split, but split by regime
  instead of direction.
- Research-only: try an HMM-fit regime classifier (returns/range/volume-vol features) against
  the twice-failed chop/trend-separation problem, scored the same way gate_audit already scores
  other gates — before assuming it's better than the current threshold-based regime detector.

**Options monitoring (independent of whether/when trading logic gets fixed)**
- Portfolio-level aggregate Greeks (net delta, daily theta bleed $, net vega/IV-pt) across the
  whole options book, not just per-position.
- Concentration flags: % of options capital by ticker and by sector, informational caps.
- New-strike / new-expiry detection: diff each held name's chain snapshot day over day, surface
  what's newly listed — useful for LEAP roll awareness without manually scanning the chain.
- Theta-decay projection chart (aggregate $ to expiry, stacked by position) and a strike ladder
  (±N strikes around each held position, held strike highlighted) — both cheap, purely
  presentational, no new data source needed beyond what's already pulled.
- Severity-tiered single alert feed (HIGH/WARN/INFO) with an early "80%-to-target" INFO tier
  before the hard target/stop alert — earlier awareness without more noise.
- IV rank computed from our own accumulated `opt_calc_log`/chain snapshots rather than (or in
  addition to) whatever the current `get_iv_rank` source is — worth checking what it uses today.

**Macro context**
- If/when Field Report graduates past log-only: consider enriching its mechanical layer with
  VIX term structure (contango/backwardation vs VIX3M) and a credit-spread z-score (HYG-TLT),
  neither of which it currently computes, and evaluate scoping a version of it specifically to
  "is this a good environment for the options book" rather than only the system-wide read.

**Explicitly not recommended without more thought**
- Do not copy the P&L framing (66k→166k) — the user was explicit this isn't the point, and
  survivorship bias in a single showcased month isn't evidence either way.
- Do not treat "switch to human-in-the-loop for options" as a conclusion — it's a live question
  the audit result raises, not a decided direction.

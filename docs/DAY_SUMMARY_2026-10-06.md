# Day summary — Oct 5 (night) → Oct 6 2026

**The analogy.** We tasted every recipe on the menu and sent most of them back. The day trader's learned model,
"know tonight who gaps tomorrow", shorting the morning's top gappers (8 years of 1-minute data), and "hand-pick stocks
that respect textbook patterns" all failed honest tests. Contrarian's dish only tasted good on the ingredients we
hand-picked. The one kitchen that cooks well is the **night shift** (Clockwork + Night Owl): it holds up on ~1,800
unselected stocks and on stocks that have since delisted. So we invested there: a **storm siren** (Basket Tide), the
two night cooks may now share ingredients, the learning cook trains every month as intended, and the scoreboard now
posts after the day's prices arrive. Plumbing: a real macro calendar and a second DataBento account that takes over
by itself.

## Shipped (live from Wed Oct 7)
| change | where | effect |
|---|---|---|
| **Basket Tide** — no new overnight entries while the WILD basket is below its 200-session average | `factory/live/basket_tide.py`, Clockwork + Night Owl `scan_and_enter` | replay 2018-26: fleet maxDD −83% → −23%, Sharpe 1.87 → 2.06; ON today (+16.6%; a ~14% fall turns it off); fails open on stale data; Telegram only when it flips |
| Night Owl may share Clockwork's names | `night_owl.py` (`MAX_NAME_USD` $6,667, `clockwork_usd`) | the skip rule cost Sharpe (1.79 vs 1.68); still skips day trader / Wave Rider / Contrarian names |
| Night Owl re-trains every calendar month | `night_owl_model.model_is_current` | was every other month; next re-train Mon Nov 2 17:30 |
| Official-price marks at 22:00 (was 17:05) | launchd `overnight_reference` | `ref_pnl` — the honest Clockwork vs Night Owl scoreboard — is no longer a night late |
| Macro calendar | `macro_calendar.py`, trades.db `macro_calendar`, launchd 19:00 | dashboard + Field Report read true dates; caught the hand-typed next FOMC wrong (Nov 4 → Oct 28) |
| DataBento key failover | `databento_keys.py` | primary ($22.28, through Dec 31) first, then the $125 reserve (through Apr 27 2027) on expiry or an account refusal |
| Dashboard | System Health + alert for the Basket Tide; calendar card; factory cards say why a scan was blocked | |

## Rejected (registry §I-§M)
- ML for the day trader — its edge is the opening print (+6.5bp at 09:35, below cost). Per-stock models < pooled.
- Predicting tomorrow's gappers tonight — evening data predicts the size of a move, not its direction; buying them −108bp.
- Shorting the morning's gappers — only the opening auction pays; 09:35 top-5 +34bp/day, t 2.5, 7/13 half-years, worst stretch −$20k on $10k.
- Textbook patterns / "stocks that respect patterns" — no pattern earns; respecting patterns is not a stable trait.
- Contrarian on unselected stocks — −36%/yr (survivorship artifact). **Kept running by user decision.**
- Macro nights: only the night before a Fed decision is reliably better (+31 vs +6bp); the CPI claim was look-ahead.

## Ready for Wed Oct 7
- 09:00-09:27 MOO sells: Clockwork CENX/AXTI/CLS, Night Owl MRVL/SMTC/UUUU (unchanged code path).
- ~09:35 Night Owl scores (Telegram picks); 15:40-15:49 both books enter under the Basket Tide — one Telegram
  "Basket Tide is live: ON".
- 22:00 official-price marks for both books.

## Bug sweep (Oct 6 night)
Fixed: per-process temp files for the tide and key state (two books can write at once); a tide-OFF day now records a
full funnel and the factory cards say "Basket Tide OFF — no entries tonight" instead of showing blanks. All suites pass:
Basket Tide (100% parity with the research gate, 2,161 sessions), Night Owl, factory, DataBento keys, reconcile guards,
options structure, dashboard render.

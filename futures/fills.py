"""Booking a fill price from IBKR's execution record — shared by both NY traders and London.

Sep 30 2026: the IBKR paper simulator sometimes fills PART of a multi-contract market order
~30 points worse than the rest, at a price the market never traded. Seen four times in two
days, always adverse, never on a 1-contract order:

    order                     lots                           market that minute (yfinance 1m)
    London exit 04:03 Sep 30  30693.25 + 30662.75            30686.00-30707.50
    NY exit    12:02 Sep 30   30881.25 + 30850.50 (both acc) 30869.25-30888.75
    London entries Sep 29     30636.50 + 30667.00 (both acc) (entry at 30636)

A real CME order for 2-4 MNQ fills within a tick or two, so lots of ONE order that disagree
by more than SPLIT_FILL_TOL_PTS are a simulator artifact, not a cost we would pay. Rule: book
every contract at the lots that agree with the one closest to the price the system expected,
and report what was set aside so the log shows it. On a real account this never triggers.
"""
from __future__ import annotations

SPLIT_FILL_TOL_PTS = 10.0


def clean_vwap(lots: list[tuple[float, float]], reference: float):
    """lots: [(contracts, price)] for ONE order; reference: the price expected at decision.
    Returns (price, set_aside) — price is None if there are no lots; set_aside lists the
    (contracts, price) lots ignored as a split-fill artifact (empty in the normal case)."""
    lots = [(float(q), float(p)) for q, p in lots if q and p]
    if not lots:
        return None, []
    prices = [p for _, p in lots]
    if max(prices) - min(prices) <= SPLIT_FILL_TOL_PTS:
        q = sum(s for s, _ in lots)
        return sum(s * p for s, p in lots) / q, []
    anchor = min(prices, key=lambda p: abs(p - reference))
    keep = [(s, p) for s, p in lots if abs(p - anchor) <= SPLIT_FILL_TOL_PTS]
    drop = [(s, p) for s, p in lots if abs(p - anchor) > SPLIT_FILL_TOL_PTS]
    q = sum(s for s, _ in keep)
    return sum(s * p for s, p in keep) / q, drop


if __name__ == '__main__':      # self-test: the four real cases above + the normal case
    assert clean_vwap([(1, 30693.25), (1, 30662.75)], 30699.25) == (30693.25, [(1.0, 30662.75)])
    assert clean_vwap([(2, 30881.25), (2, 30850.50)], 30880.75)[0] == 30881.25
    assert clean_vwap([(1, 30636.50), (1, 30667.00)], 30636.25)[0] == 30636.50
    assert clean_vwap([(1, 100.0), (1, 101.0)], 100.0) == (100.5, [])      # real 1pt split kept
    assert clean_vwap([], 1.0) == (None, [])
    print('fills.clean_vwap self-test OK')

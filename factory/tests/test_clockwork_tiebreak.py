"""Clockwork tie-break tests (Oct 9 2026). Run:  venv/bin/python -m factory.tests.test_clockwork_tiebreak
Consistency is a count out of 30, so names tie at the cut. Ties must be broken by Night Owl's score, then a
date-seeded shuffle — never by the WILD list's alphabetical order (RESEARCH_REGISTRY §Q6)."""
import sys
sys.path.insert(0, '/Users/sushil/trading')
from factory.live import overnight as CW

FAILS = []


def check(name, ok, detail=''):
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ''))
    if not ok:
        FAILS.append(name)


def _with_scores(scores):
    orig = CW.nightowl_scores
    CW.nightowl_scores = lambda day: dict(scores)
    return orig


def test_consistency_still_primary():
    orig = _with_scores({'AAA': -1.0, 'ZZZ': 5.0})
    try:
        r = CW.rank_candidates({'AAA': 0.73, 'ZZZ': 0.70}, '2026-10-12')
        check('higher consistency wins regardless of Night Owl', [s for s, _ in r] == ['AAA', 'ZZZ'], str(r))
    finally:
        CW.nightowl_scores = orig


def test_ties_by_night_owl():
    orig = _with_scores({'ACLS': 0.01, 'CENX': 0.02, 'PI': 0.05, 'SMTC': 0.03})
    try:
        sig = {'ACLS': 0.70, 'CENX': 0.70, 'PI': 0.70, 'SMTC': 0.70, 'AXTI': 0.60}
        r = [s for s, _ in CW.rank_candidates(sig, '2026-10-12')]
        check('tied names ordered by Night Owl score', r[:4] == ['PI', 'SMTC', 'CENX', 'ACLS'], str(r))
        check('lower consistency stays below the tie', r[-1] == 'AXTI', str(r))
    finally:
        CW.nightowl_scores = orig


def test_unscored_after_scored():
    orig = _with_scores({'ZZZ': -0.5})
    try:
        r = [s for s, _ in CW.rank_candidates({'AAA': 0.7, 'ZZZ': 0.7}, '2026-10-12')]
        check('a scored name beats an unscored one in a tie', r == ['ZZZ', 'AAA'], str(r))
    finally:
        CW.nightowl_scores = orig


def test_no_scores_not_alphabetical():
    """Without Night Owl scores the shuffle decides — over many days the first-ranked name must vary and
    must not favour early letters."""
    orig = _with_scores({})
    try:
        names = ['ACLS', 'AXTI', 'CENX', 'CLS', 'LITE', 'P', 'PI', 'SMTC']
        firsts = []
        for d in range(1, 29):
            r = CW.rank_candidates({n: 0.70 for n in names}, f'2026-11-{d:02d}')
            firsts.append(r[0][0])
        ac = sum(f[0] in 'ABC' for f in firsts) / len(firsts)
        check('shuffle varies by day', len(set(firsts)) >= 4, f'{sorted(set(firsts))}')
        check('shuffle not biased to A-C', ac < 0.75, f'A-C first {ac:.0%} (A-C share of names 50%)')
        r1 = CW.rank_candidates({n: 0.70 for n in names}, '2026-11-02')
        r2 = CW.rank_candidates({n: 0.70 for n in names}, '2026-11-02')
        check('shuffle reproducible for a given day', r1 == r2)
    finally:
        CW.nightowl_scores = orig


def test_real_scores_readable():
    s = CW.nightowl_scores('2026-10-09')
    check('reads night_owl_scores from trades.db', len(s) > 50, f'{len(s)} names scored on 2026-10-09')


if __name__ == '__main__':
    test_consistency_still_primary(); test_ties_by_night_owl(); test_unscored_after_scored()
    test_no_scores_not_alphabetical(); test_real_scores_readable()
    print(f"\n{'ALL PASS' if not FAILS else 'FAILED: ' + ', '.join(FAILS)}")
    sys.exit(1 if FAILS else 0)

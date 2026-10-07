"""Broad US common-stock daily data for the gapper study (Oct 5 2026, RESEARCH_REGISTRY §J).

Our 241-name universe almost never produces the morning's top gainers (XRPN, SDEV, IBRX were all outside
it), so this pulls EVERY currently-listed US common stock from the Nasdaq Trader symbol directory and
downloads yfinance daily bars (split/dividend adjusted) from 2024-01-01 (or START/END/OUT given on the command line).

⚠️ SURVIVORSHIP: only names listed TODAY. Pump-and-dump names that were later delisted are missing — and
they are exactly the names whose crashes would drag a "buy the frenzy" result down. Treat any LONG result
from this panel as an upper bound.

Writes research_out/broad_daily_2024.parquet (date, symbol, open, high, low, close, volume).
Usage: venv/bin/python research_gappers_data.py
"""
import io, os, sys, time, warnings
import pandas as pd, requests
warnings.filterwarnings('ignore')
ROOT = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, ROOT)
OUT = os.path.join(ROOT, 'research_out', 'broad_daily_2024.parquet')
START, END = '2024-01-01', None   # override: python research_gappers_data.py 2018-01-01 2024-01-10 research_out/broad_daily_2018.parquet
BAD_NAME = ('Warrant', ' Unit', ' Right', 'Preferred', ' Notes', 'Debenture', '%', 'Depositary Shares, each representing a',
            'Trust Preferred', 'Subordinated', 'When Issued', 'Acquisition Corp')


def symbols():
    a = pd.read_csv(io.StringIO(requests.get('https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt', timeout=30).text), sep='|')
    b = pd.read_csv(io.StringIO(requests.get('https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt', timeout=30).text), sep='|')
    a = a[(a['ETF'] == 'N') & (a['Test Issue'] == 'N')].rename(columns={'Symbol': 'sym'})
    b = b[(b['ETF'] == 'N') & (b['Test Issue'] == 'N')].rename(columns={'ACT Symbol': 'sym'})
    d = pd.concat([a[['sym', 'Security Name']], b[['sym', 'Security Name']]]).dropna()
    d = d[~d['sym'].str.contains(r'[\.\$\^ ]', regex=True)]
    d = d[~d['Security Name'].str.contains('|'.join(BAD_NAME), regex=True)]
    return sorted(set(d['sym']))


def main():
    import yf_cache_fix  # noqa: F401
    import yfinance as yf
    syms = symbols()
    print(f'{len(syms)} common-stock symbols', flush=True)
    frames, t0 = [], time.time()
    for i in range(0, len(syms), 150):
        chunk = syms[i:i + 150]
        for attempt in range(3):
            try:
                df = yf.download(chunk, start=START, end=END, auto_adjust=True, progress=False, group_by='ticker', threads=True)
                break
            except Exception as e:
                print('retry', e, flush=True); time.sleep(10)
        errs = ' '.join(str(v) for v in getattr(yf.shared, '_ERRORS', {}).values())
        if 'RateLimit' in errs or 'Too Many Requests' in errs:
            print('RATE LIMITED — stopping to protect the live books', flush=True)
            break
        time.sleep(6)                     # throttle: the Oct 5 rate-limit lesson
        for s in chunk:
            try:
                x = df[s].dropna(how='all')
            except Exception:
                continue
            if len(x) < 40:
                continue
            x = x.rename(columns=str.lower)[['open', 'high', 'low', 'close', 'volume']]
            x['symbol'] = s; x.index.name = 'date'
            frames.append(x.reset_index())
        print(f'  {min(i + 150, len(syms))}/{len(syms)}  kept {len(frames)}  {time.time() - t0:.0f}s', flush=True)
    d = pd.concat(frames, ignore_index=True)
    d['date'] = pd.to_datetime(d['date'])
    if getattr(d['date'].dt, 'tz', None) is not None:
        d['date'] = d['date'].dt.tz_localize(None)
    d.to_parquet(OUT, index=False)
    print(f'wrote {OUT}: {len(d):,} rows, {d.symbol.nunique()} symbols, {d.date.min().date()} → {d.date.max().date()}')


if __name__ == '__main__':
    if len(sys.argv) >= 4:
        START, END, OUT = sys.argv[1], sys.argv[2], os.path.join(ROOT, sys.argv[3])
    main()

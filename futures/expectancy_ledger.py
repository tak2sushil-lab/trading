#!/usr/bin/env python
"""
Expectancy Ledger — nightly learning loop (added Jul 18 2026, redesign build ②).

Three jobs, all OFFLINE (no live-code risk, constitution Article 7):

1. SHADOW FISH-NET — evaluates the decoder-fade book discovered Jul 18 2026:
   fade the decoder's raw LONG signals (first minute of each signal streak),
   one position at a time, 60-min time exit, hard stop 60pts. Mined result
   (Jun 18–Jul 17, episode-level, IS/OOS split at Jul 6): fade-LONG +34.1pts
   IS / +15.3pts OOS per episode; fade-SHORT ~0 (not traded). Writes each
   shadow trade to `shadow_fishnet` in trades.db. This book NEVER places
   orders — it must earn 30+ days of positive shadow expectancy plus a green
   trailing health before promotion is even discussed.

2. DECODER CONTEXT JOIN — attaches the nearest decoder snapshot (phase, flow,
   adx, vwap_ext, vwap_slope, vol_label, range_pos) to every gate_blocks row
   into `gate_blocks_ctx`. This is the feature store for the future scored
   entry model (train only after ~60 days of joined data).

3. LEDGER REPORT — trailing 10-trading-day expectancy per live book
   (equity LONG / equity SHORT / futures NY / London / shadow fish-net),
   printed and appended to logs/expectancy_ledger.log.

Run nightly via launchd (com.sushil.trading.expectancy_ledger) after the
decoder stops writing (22:35 ET) — or manually:
    venv/bin/python futures/expectancy_ledger.py
"""
import json, os, sqlite3, sys
from datetime import datetime, timedelta

import pandas as pd
import numpy as np
import pytz

ET   = pytz.timezone('America/New_York')
_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_DIR)
TRADES_DB = os.path.join(ROOT, 'trades.db')
MARKET_DB = os.path.join(ROOT, 'market_data.db')
DECODER_JSONL = os.path.join(ROOT, 'logs', 'decoder_data.jsonl')
LOG_PATH = os.path.join(ROOT, 'logs', 'expectancy_ledger.log')

FISHNET_STOP_PTS   = 60.0   # hard stop on the shadow fade position
FISHNET_HOLD_MIN   = 60     # time exit
FISHNET_FADE_SIDES = ('LONG',)  # fade only decoder LONG signals (SHORT fade ~0 edge)


def log(msg):
    line = f"[{datetime.now(ET).strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line)
    with open(LOG_PATH, 'a') as f:
        f.write(line + '\n')


def load_decoder(min_date=None):
    recs = []
    with open(DECODER_JSONL) as f:
        for line in f:
            try:
                r = json.loads(line)
            except Exception:
                continue
            if 'type' in r or 'ts' not in r:
                continue
            if min_date and r.get('date', '') < min_date:
                continue
            recs.append(r)
    d = pd.DataFrame(recs)
    if d.empty:
        return d
    d['ts'] = pd.to_datetime(d['ts'], format='ISO8601', utc=True).dt.tz_convert(ET)
    return d.sort_values('ts').reset_index(drop=True)


def load_1m_bars(min_date):
    con = sqlite3.connect(MARKET_DB)
    b = pd.read_sql(
        "select ts_utc, close, high, low from futures_bars_1m "
        "where symbol='MNQ' and ts_utc>=?", con, params=(min_date,))
    con.close()
    if b.empty:
        return b
    b['ts'] = pd.to_datetime(b['ts_utc'], format='ISO8601', utc=True).dt.tz_convert(ET)
    return b.sort_values('ts').set_index('ts')


def ensure_tables(con):
    con.execute("""CREATE TABLE IF NOT EXISTS shadow_fishnet (
        entry_ts TEXT PRIMARY KEY, faded_signal TEXT, side TEXT,
        entry_price REAL, exit_ts TEXT, exit_price REAL, exit_reason TEXT,
        pnl_pts REAL, pnl_usd REAL, mae_pts REAL, mfe_pts REAL,
        phase TEXT, flow TEXT, adx REAL, vwap_ext REAL, created_at TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS gate_blocks_ctx (
        gate_block_id INTEGER PRIMARY KEY, phase TEXT, flow TEXT, adx REAL,
        vwap_ext REAL, vwap_slope REAL, vol_label TEXT, range_pos REAL,
        rvol REAL, snapshot_ts TEXT)""")
    con.commit()


def run_shadow_fishnet(con, dec, bars):
    """Simulate the fade book on days not yet in shadow_fishnet."""
    done = {r[0][:10] for r in con.execute("select entry_ts from shadow_fishnet")}
    dec = dec.copy()
    dec['d'] = dec['ts'].dt.date.astype(str)
    today = datetime.now(ET).strftime('%Y-%m-%d')
    px = bars['close']
    n_new = 0
    for d, day in dec.groupby('d'):
        if d in done or d >= today:      # today may still be partial
            continue
        day = day.sort_values('ts')
        day['prev_sig'] = day['signal'].shift(1)
        episodes = day[(day['signal'].isin(FISHNET_FADE_SIDES)) &
                       (day['signal'] != day['prev_sig'])]
        open_until = None
        for _, ep in episodes.iterrows():
            if open_until is not None and ep['ts'] <= open_until:
                continue                  # one position at a time
            entry_ts = ep['ts'] + timedelta(minutes=1)
            fut = bars[(bars.index >= entry_ts) &
                       (bars.index <= entry_ts + timedelta(minutes=FISHNET_HOLD_MIN))]
            if len(fut) < 5:
                continue
            e = float(fut.iloc[0]['close'])
            side = 'SHORT' if ep['signal'] == 'LONG' else 'LONG'
            sign = -1 if side == 'SHORT' else 1
            stop = e - sign * FISHNET_STOP_PTS
            exit_price, exit_reason, exit_ts = None, 'time', fut.index[-1]
            mae = mfe = 0.0
            for ts_, bar in fut.iloc[1:].iterrows():
                adverse = (bar['high'] - e) if side == 'SHORT' else (e - bar['low'])
                favor   = (e - bar['low']) if side == 'SHORT' else (bar['high'] - e)
                mae = max(mae, adverse); mfe = max(mfe, favor)
                if adverse >= FISHNET_STOP_PTS:
                    exit_price, exit_reason, exit_ts = stop, 'stop', ts_
                    break
            if exit_price is None:
                exit_price = float(fut.iloc[-1]['close'])
            pnl = sign * (exit_price - e)
            con.execute("insert or ignore into shadow_fishnet values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (str(entry_ts), ep['signal'], side, e, str(exit_ts), exit_price,
                         exit_reason, round(pnl, 2), round(pnl * 2, 2), round(mae, 2),
                         round(mfe, 2), ep.get('phase'), ep.get('flow'),
                         ep.get('adx'), ep.get('vwap_ext'),
                         datetime.now(ET).isoformat()))
            n_new += 1
            open_until = exit_ts
    con.commit()
    log(f"shadow_fishnet: +{n_new} trades")


def run_context_join(con, dec):
    gb = pd.read_sql("select id, ts from gate_blocks where id not in "
                     "(select gate_block_id from gate_blocks_ctx)", con)
    if gb.empty or dec.empty:
        log("context join: nothing new")
        return
    gb['ts'] = pd.to_datetime(gb['ts'], format='ISO8601', utc=True).dt.tz_convert(ET)
    gb = gb.sort_values('ts')
    cols = ['ts', 'phase', 'flow', 'adx', 'vwap_ext', 'vwap_slope',
            'vol_label', 'range_pos', 'rvol']
    j = pd.merge_asof(gb, dec[cols].sort_values('ts'), on='ts',
                      direction='backward', tolerance=pd.Timedelta('3min'))
    n = 0
    for _, r in j.iterrows():
        if pd.isna(r.get('phase')):
            continue
        con.execute("insert or ignore into gate_blocks_ctx values (?,?,?,?,?,?,?,?,?,?)",
                    (int(r['id']), r['phase'], r['flow'], r['adx'], r['vwap_ext'],
                     r['vwap_slope'], r['vol_label'], r['range_pos'], r['rvol'],
                     str(r['ts'])))
        n += 1
    con.commit()
    log(f"context join: +{n} rows -> gate_blocks_ctx")


def trailing(con, sql, params=()):
    rows = con.execute(sql, params).fetchall()
    if not rows:
        return None
    pnl = [r[0] for r in rows if r[0] is not None]
    if not pnl:
        return None
    return len(pnl), sum(pnl), (sum(1 for p in pnl if p > 0) / len(pnl))


def report(con):
    cutoff = (datetime.now(ET) - timedelta(days=14)).strftime('%Y-%m-%d')
    books = {
        'equity LONG':  ("select pnl from trades where side='LONG' and entry_date>=? and setup_type!='RECONCILED'", (cutoff,)),
        'equity SHORT': ("select pnl from trades where side='SHORT' and entry_date>=? and setup_type!='RECONCILED'", (cutoff,)),
        'futures NY (IBKR)': ("select pnl from futures_trades where entry_date>=? and account_mode='IBKR' and setup_type!='RECONCILED'", (cutoff,)),
        'London (live paper)': ("select pnl from london_trades where entry_date>=?", (cutoff,)),
        'shadow fish-net': ("select pnl_usd from shadow_fishnet where entry_ts>=?", (cutoff,)),
    }
    lines = [f"═══ EXPECTANCY LEDGER — trailing 14 calendar days (to {datetime.now(ET).strftime('%Y-%m-%d')}) ═══"]
    for name, (sql, params) in books.items():
        r = trailing(con, sql, params)
        if r is None:
            lines.append(f"  {name:<22} no trades")
        else:
            n, total, wr = r
            lines.append(f"  {name:<22} n={n:<4} ${total:+8.0f}  wr={wr:.0%}  avg=${total/n:+.1f}")
    txt = '\n'.join(lines)
    log(txt)
    return txt


def main():
    dec = load_decoder()
    if dec.empty:
        log("no decoder data — abort")
        return
    bars = load_1m_bars(dec['ts'].min().strftime('%Y-%m-%d'))
    con = sqlite3.connect(TRADES_DB)
    ensure_tables(con)
    run_shadow_fishnet(con, dec, bars)
    run_context_join(con, dec)
    report(con)
    con.close()


if __name__ == '__main__':
    main()

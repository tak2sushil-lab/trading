# learner.py — learning engine
# Runs overnight, analyses all trades, updates strategy weights
# The more trades, the smarter the screener becomes
# Command: python learner.py (or runs from scheduler)

from dotenv import load_dotenv
load_dotenv()

import os
import sqlite3
from datetime import datetime, date, timedelta
import pytz
from database import (
    get_recent_trades, get_best_setups,
    update_strategy_weights, get_win_rate, get_connection,
    update_sector_grades,
)

ET = pytz.timezone('America/New_York')

def analyse_rsi_performance():
    """Which RSI ranges had best win rate?"""
    conn = get_connection()
    c    = conn.cursor()
    c.execute('''
        SELECT
            CASE
                WHEN rsi_at_entry < 35 THEN 'oversold <35'
                WHEN rsi_at_entry < 45 THEN 'recovering 35-45'
                WHEN rsi_at_entry < 55 THEN 'neutral 45-55'
                WHEN rsi_at_entry < 65 THEN 'momentum 55-65'
                WHEN rsi_at_entry < 75 THEN 'hot 65-75'
                ELSE 'overbought 75+'
            END as rsi_range,
            COUNT(*) as total,
            SUM(CASE WHEN status='WIN' THEN 1 ELSE 0 END) as wins,
            AVG(pnl) as avg_pnl
        FROM trades
        WHERE status IN ('WIN','LOSS')
        AND rsi_at_entry IS NOT NULL
        GROUP BY rsi_range
        ORDER BY wins*1.0/total DESC
    ''')
    rows = c.fetchall()
    conn.close()
    return rows

def analyse_volume_performance():
    """Which volume ratios had best results?"""
    conn = get_connection()
    c    = conn.cursor()
    c.execute('''
        SELECT
            CASE
                WHEN volume_ratio < 1.5 THEN 'low <1.5x'
                WHEN volume_ratio < 2.0 THEN 'moderate 1.5-2x'
                WHEN volume_ratio < 3.0 THEN 'high 2-3x'
                ELSE 'surge 3x+'
            END as vol_range,
            COUNT(*) as total,
            SUM(CASE WHEN status='WIN' THEN 1 ELSE 0 END) as wins,
            AVG(pnl) as avg_pnl
        FROM trades
        WHERE status IN ('WIN','LOSS')
        AND volume_ratio IS NOT NULL
        GROUP BY vol_range
        ORDER BY wins*1.0/total DESC
    ''')
    rows = c.fetchall()
    conn.close()
    return rows

def analyse_sector_performance():
    """Which sectors are performing best recently?"""
    conn = get_connection()
    c    = conn.cursor()
    c.execute('''
        SELECT sector,
               COUNT(*) as total,
               SUM(CASE WHEN status='WIN' THEN 1 ELSE 0 END) as wins,
               AVG(pnl) as avg_pnl
        FROM trades
        WHERE status IN ('WIN','LOSS')
        AND entry_date >= date('now', '-14 days')
        AND sector IS NOT NULL
        GROUP BY sector
        HAVING total >= 2
        ORDER BY wins*1.0/total DESC
    ''')
    rows = c.fetchall()
    conn.close()
    return rows

def analyse_earnings_performance():
    """Do we do better/worse near earnings?"""
    conn = get_connection()
    c    = conn.cursor()
    c.execute('''
        SELECT
            CASE
                WHEN earnings_days < 7 THEN 'near earnings <7d'
                WHEN earnings_days < 14 THEN 'approaching 7-14d'
                ELSE 'safe 14d+'
            END as earnings_zone,
            COUNT(*) as total,
            SUM(CASE WHEN status='WIN' THEN 1 ELSE 0 END) as wins,
            AVG(pnl) as avg_pnl
        FROM trades
        WHERE status IN ('WIN','LOSS')
        AND earnings_days IS NOT NULL
        GROUP BY earnings_zone
        ORDER BY wins*1.0/total DESC
    ''')
    rows = c.fetchall()
    conn.close()
    return rows

def calculate_new_weights(rsi_analysis, volume_analysis,
                           earnings_analysis, min_trades=3):
    """
    Calculate updated strategy weights based on what's working
    Weights range from 0.5 (less important) to 2.0 (very important)
    """
    weights = {
        'rsi':      1.0,
        'volume':   1.0,
        'momentum': 1.0,
        'sector':   1.0,
        'earnings': 1.0
    }

    # ── RSI weight ────────────────────────────────────────
    if rsi_analysis:
        total_trades = sum(r[1] for r in rsi_analysis)
        total_wins   = sum(r[2] for r in rsi_analysis)
        overall_rate = total_wins / total_trades if total_trades > 0 else 0.5

        # Best RSI range win rate
        best_rsi = max(rsi_analysis, key=lambda x: x[2]/x[1] if x[1] > 0 else 0)
        best_rate = best_rsi[2] / best_rsi[1] if best_rsi[1] > 0 else 0.5

        if best_rate > 0.58:  # lowered from 0.65 — fires at our actual 54-60% WR
            weights['rsi'] = 1.5  # RSI is a strong predictor
        elif best_rate < 0.45:
            weights['rsi'] = 0.7  # RSI not working well lately

    # ── Volume weight ─────────────────────────────────────
    if volume_analysis:
        # Check if high volume trades win more
        high_vol = [r for r in volume_analysis if '2-3x' in r[0] or '3x+' in r[0]]
        low_vol  = [r for r in volume_analysis if '1.5x' in r[0]]

        if high_vol and low_vol:
            high_rate = sum(r[2] for r in high_vol) / sum(r[1] for r in high_vol)
            low_rate  = sum(r[2] for r in low_vol) / sum(r[1] for r in low_vol)

            if high_rate > low_rate + 0.08:  # lowered from 0.15 — fires at our WR spread
                weights['volume'] = 1.7  # Volume very predictive
            elif high_rate < low_rate:
                weights['volume'] = 0.8

    # ── Earnings weight ───────────────────────────────────
    if earnings_analysis:
        near = [r for r in earnings_analysis if 'near' in r[0]]
        safe = [r for r in earnings_analysis if 'safe' in r[0]]

        if near and safe:
            near_rate = near[0][2] / near[0][1] if near[0][1] > 0 else 0.5
            safe_rate = safe[0][2] / safe[0][1] if safe[0][1] > 0 else 0.5

            if safe_rate > near_rate + 0.1:
                weights['earnings'] = 1.5  # Earnings matter a lot
            else:
                weights['earnings'] = 0.8  # Earnings not hurting us

    return weights

def analyse_sector_grades():
    """
    Compute 30-day WR per sector, classify STRONG/NEUTRAL/WEAK.
    Requires min 5 trades to avoid noise from thin data.
    """
    conn = get_connection()
    rows = conn.execute('''
        SELECT sector,
               COUNT(*) as total,
               SUM(CASE WHEN status='WIN' THEN 1 ELSE 0 END) as wins
        FROM trades
        WHERE status IN ('WIN','LOSS')
          AND entry_date >= date('now', '-30 days')
          AND sector IS NOT NULL AND sector != 'OTHER'
        GROUP BY sector
        HAVING total >= 5
        ORDER BY wins * 1.0 / total DESC
    ''').fetchall()
    conn.close()

    grades = {}
    for sector, total, wins in rows:
        wr    = wins / total
        grade = 'STRONG' if wr >= 0.70 else 'WEAK' if wr < 0.50 else 'NEUTRAL'
        grades[sector] = {'wr': wr, 'count': total, 'grade': grade}
        print(f"  Sector {sector:<15} {wr*100:.0f}% WR ({total} trades) → {grade}")
    return grades


def run_learning_cycle():
    """Main learning function — runs overnight"""
    print(f"\n{'='*55}")
    print(f"🧠 Learning Cycle — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*55}")

    # Check if we have enough data
    conn = get_connection()
    c    = conn.cursor()
    c.execute("SELECT COUNT(*) FROM trades WHERE status IN ('WIN','LOSS')")
    trade_count = c.fetchone()[0]
    conn.close()

    print(f"Total completed trades in database: {trade_count}")

    if trade_count < 30:
        print(f"Not enough trades to learn from yet ({trade_count}/30). Keep collecting data.")
        return

    # Run all analyses
    print("\n--- RSI Analysis ---")
    rsi_analysis = analyse_rsi_performance()
    for r in rsi_analysis:
        if r[1] > 0:
            rate = r[2]/r[1]*100
            print(f"  {r[0]:<25} Win rate: {rate:.0f}% ({r[1]} trades, avg ${r[3]:.2f})")

    print("\n--- Volume Analysis ---")
    vol_analysis = analyse_volume_performance()
    for r in vol_analysis:
        if r[1] > 0:
            rate = r[2]/r[1]*100
            print(f"  {r[0]:<25} Win rate: {rate:.0f}% ({r[1]} trades, avg ${r[3]:.2f})")

    print("\n--- Sector Analysis (last 14 days) ---")
    sector_analysis = analyse_sector_performance()
    for r in sector_analysis:
        if r[1] > 0:
            rate = r[2]/r[1]*100
            print(f"  {r[0]:<20} Win rate: {rate:.0f}% ({r[1]} trades, avg ${r[3]:.2f})")

    print("\n--- Sector Grades (30d, min 5 trades) ---")
    sector_grades = analyse_sector_grades()
    if sector_grades:
        update_sector_grades(sector_grades)
        print(f"  Saved {len(sector_grades)} sector grades to DB")

    print("\n--- Earnings Analysis ---")
    earnings_analysis = analyse_earnings_performance()
    for r in earnings_analysis:
        if r[1] > 0:
            rate = r[2]/r[1]*100
            print(f"  {r[0]:<25} Win rate: {rate:.0f}% ({r[1]} trades, avg ${r[3]:.2f})")

    print("\n--- Best Setups ---")
    best_setups = get_best_setups(min_trades=3)
    for r in best_setups:
        if r[1] > 0:
            rate = r[2]/r[1]*100
            print(f"  {r[0]:<25} Win rate: {rate:.0f}% ({r[1]} trades, avg ${r[3]:.2f})")

    # Calculate and save new weights
    new_weights = calculate_new_weights(
        rsi_analysis, vol_analysis, earnings_analysis
    )

    overall_wr = get_win_rate(days=30)
    notes = (
        f"Updated from {trade_count} trades. "
        f"Overall 30d win rate: {overall_wr:.0f}%. "
        f"Top sector: {sector_analysis[0][0] if sector_analysis else 'N/A'}"
    )

    update_strategy_weights(new_weights, notes)

    print(f"\n✅ Updated strategy weights:")
    print(f"  RSI weight:      {new_weights['rsi']:.1f}")
    print(f"  Volume weight:   {new_weights['volume']:.1f}")
    print(f"  Momentum weight: {new_weights['momentum']:.1f}")
    print(f"  Sector weight:   {new_weights['sector']:.1f}")
    print(f"  Earnings weight: {new_weights['earnings']:.1f}")
    print(f"\n  Overall 30-day win rate: {overall_wr:.0f}%")
    print(f"  System is now smarter for tomorrow!")

if __name__ == '__main__':
    run_learning_cycle()

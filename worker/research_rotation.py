"""Forskning: relativ styrka / cross-sectional momentum-rotation (long-only).

Vid varje ombalansering: rangordna coins efter momentum (avkastning senaste L dagar),
håll de top-K starkaste likaviktat (bara positiva momentum, annars cash). Ombalansera
var R:e dag. Kostnad tas på omsättningen.

Använder daily-OHLCV från DB. Robusthetskoll över flera lookbacks (inte tuning-till-fit:
en edge ska finnas över ett SPANN av rimliga parametrar, inte bara en magisk siffra).

Varning: använder dagens 18 coins → survivorship bias finns (gynnsam). Klarar den inte
ens MED den fördelen är slutsatsen tydlig.
"""
import numpy as np
import pandas as pd

import backtest_metrics as metrics
import config
import db


def load_panel(conn) -> pd.DataFrame:
    coin_ids = db.load_coin_ids(conn)
    cols = {}
    for coin in config.UNIVERSE:
        cid = coin_ids.get(coin.symbol)
        if cid is None:
            continue
        df = db.load_ohlcv_df(conn, cid, "1d")
        if len(df) > 100:
            cols[coin.symbol] = df["close"]
    panel = pd.concat(cols, axis=1).sort_index()
    return panel


def rotation(panel: pd.DataFrame, lookback: int, rebalance: int, top_k: int, cost: float) -> pd.Series:
    rets = panel.pct_change()
    dates = panel.index
    equity, weights = 1.0, pd.Series(0.0, index=panel.columns)
    curve = {}
    for i, d in enumerate(dates):
        if i > 0:  # gårdagens vikter tjänar dagens avkastning (ingen lookahead)
            equity *= 1 + float((weights * rets.iloc[i].fillna(0)).sum())
        if i >= lookback and i % rebalance == 0:
            mom = (panel.iloc[i] / panel.iloc[i - lookback] - 1).dropna()
            ranked = mom.sort_values(ascending=False)
            selected = [s for s in ranked.index[:top_k] if mom[s] > 0]
            new_w = pd.Series(0.0, index=panel.columns)
            if selected:
                new_w[selected] = 1.0 / len(selected)
            equity *= 1 - cost * float((new_w - weights).abs().sum())
            weights = new_w
        curve[d] = equity
    return pd.Series(curve, name="equity")


def _m(eq, label):
    m = metrics.compute(eq, [], "1d")
    return f"  {label:<22} return {m['total_return']*100:>8.0f}%  cagr {m['cagr']*100:>6.0f}%  sharpe {m['sharpe']:>5.2f}  maxDD {m['max_drawdown']*100:>5.0f}%"


def main(conn):
    panel = load_panel(conn)
    print(f"Panel: {panel.shape[1]} coins, {panel.shape[0]} dagar "
          f"({panel.index.min().date()} → {panel.index.max().date()})\n")

    print("=== 1) Robusthet över lookbacks (rebalance=7d, top_k=4, kostnad x1) ===")
    for lb in (20, 40, 60, 90):
        eq = rotation(panel, lookback=lb, rebalance=7, top_k=4, cost=0.001)
        print(_m(eq, f"lookback={lb}d"))

    print("\n=== 2) Kostnadskänslighet (lookback=40, rebalance=7, top_k=4) ===")
    for mult in (1, 2, 3):
        eq = rotation(panel, 40, 7, 4, cost=0.001 * mult)
        print(_m(eq, f"kostnad x{mult}"))

    print("\n=== 3) Per år (lookback=40, rebalance=7, top_k=4, x1) ===")
    eq = rotation(panel, 40, 7, 4, cost=0.001)
    bh = (panel / panel.iloc[0]).mean(axis=1)  # likaviktad buy&hold
    btc = panel["BTC"] / panel["BTC"].iloc[0] if "BTC" in panel else None
    for y in sorted(set(eq.index.year)):
        e = eq[eq.index.year == y]
        b = bh[bh.index.year == y]
        bt = btc[btc.index.year == y] if btc is not None else None
        if len(e) < 2:
            continue
        sr = e.iloc[-1] / e.iloc[0] - 1
        br = b.iloc[-1] / b.iloc[0] - 1
        btr = (bt.iloc[-1] / bt.iloc[0] - 1) if bt is not None and len(bt) >= 2 else float("nan")
        print(f"  {y}:  rotation {sr*100:>7.0f}%   buy&hold-univ {br*100:>7.0f}%   BTC {btr*100:>7.0f}%")

    print("\n=== 4) Hela perioden vs baseline (lookback=40, x1) ===")
    print(_m(eq, "Rotation"))
    print(_m(bh, "Buy&Hold univ"))
    if btc is not None:
        print(_m(btc, "Buy&Hold BTC"))

    print("\n— Tolkning —")
    print("  Grön över FLERA lookbacks + överlever x2 + slår buy&hold = första äkta tecknet.")
    print("  Bara en lookback funkar, eller faller vid x2 = inte robust.")

"""Survivorship-stresstest för rotation.

Lägger till "legit-men-döda" coins (var topp-tier med use case, gick mot noll) i
kandidatpoolen och kör om rotation. Om strategin hade roterat in i dem och sprängts →
mycket av edgen var survivorship bias, inte äkta.

Hämtar dödskandidaterna lokalt via ccxt (de finns inte i DB). Detta är inte ett komplett
point-in-time-universum, men det stresstestar exakt oron: hade momentum köpt minorna?
"""
import ccxt
import pandas as pd

import config
import db
import research_rotation as rot

# Var topp-30-ish med use case, kraschade katastrofalt (~90–100%) 2021–2026.
DEAD_CANDIDATES = [
    "LUNC/USDT",   # Terra Luna Classic — algoritmisk stablecoin, topp-8 → ~0 (maj 2022)
    "FTT/USDT",    # FTX Token — börstoken, → ~0 (nov 2022)
    "WAVES/USDT",  # Waves — smart-contract-plattform, kraschade ~99%
    "CEL/USDT",    # Celsius — CeFi-token, konkurs
    "OMG/USDT",    # OMG Network — topp-30 en gång, avtynade
]


def _fetch(ex, symbol, since_ms, limit=1000):
    out, interval = [], 86400 * 1000
    while True:
        batch = ex.fetch_ohlcv(symbol, "1d", since=since_ms, limit=limit)
        if not batch:
            break
        if out and batch[0][0] <= out[-1][0]:
            batch = [b for b in batch if b[0] > out[-1][0]]
            if not batch:
                break
        out.extend(batch)
        since_ms = batch[-1][0] + interval
        if len(batch) < limit:
            break
    return out


def fetch_dead(ex, since_ms) -> dict:
    got = {}
    for sym in DEAD_CANDIDATES:
        if sym not in ex.markets:
            print(f"  {sym}: finns inte på börsen (avlistad) — hoppar")
            continue
        bars = _fetch(ex, sym, since_ms)
        if len(bars) < 50:
            print(f"  {sym}: för lite data")
            continue
        idx = pd.to_datetime([b[0] for b in bars], unit="ms", utc=True)
        s = pd.Series([b[4] for b in bars], index=idx)
        got[sym.split("/")[0]] = s
        print(f"  {sym}: {len(s)} dagar ({idx.min().date()} → {idx.max().date()}, "
              f"sista pris {s.iloc[-1]:g})")
    return got


def _line(panel, label, cost):
    eq = rot.rotation(panel, 40, 7, 4, cost)
    import backtest_metrics as metrics
    m = metrics.compute(eq, [], "1d")
    return f"  {label:<28} return {m['total_return']*100:>8.0f}%  sharpe {m['sharpe']:>5.2f}  maxDD {m['max_drawdown']*100:>5.0f}%", eq


def main(conn):
    base = rot.load_panel(conn)
    ex = ccxt.binance({"enableRateLimit": True})
    ex.load_markets()
    since = ex.parse8601("2021-01-01T00:00:00Z")

    print("Hämtar döda/kraschade legit-coins:")
    dead = fetch_dead(ex, since)
    if not dead:
        print("Inga dödskandidater kunde hämtas (data finns inte längre) — i sig en lärdom.")
        return

    extended = pd.concat({**{c: base[c] for c in base.columns}, **dead}, axis=1).sort_index()
    print(f"\nBas: {base.shape[1]} coins.  Med döda: {extended.shape[1]} coins ({list(dead)}).\n")

    print("=== Rotation: UTAN vs MED de döda coinsen ===")
    for cost, tag in ((0.001, "x1"), (0.002, "x2")):
        l1, _ = _line(base, f"Bas (överlevare), {tag}", cost)
        l2, eq_ext = _line(extended, f"+ döda coins, {tag}", cost)
        print(l1)
        print(l2)
        print()

    print("=== Per år MED döda coins (x1) ===")
    eq = rot.rotation(extended, 40, 7, 4, 0.001)
    for y in sorted(set(eq.index.year)):
        e = eq[eq.index.year == y]
        if len(e) >= 2:
            print(f"  {y}:  {(e.iloc[-1]/e.iloc[0]-1)*100:>7.0f}%")

    print("\n— Tolkning —")
    print("  Om 'MED döda' är mycket sämre än 'Bas' → edgen var till stor del survivorship.")
    print("  Om de ligger nära varandra → rotation undvek minorna även i realtid (mer äkta).")

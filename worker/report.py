"""Veckorapport: boten betygsätter sina EGNA flaggor mot marknaden.

Poängen är inte att se bra ut — det är att vi ska upptäcka när en signal slutar
fungera utan att någon behöver be om en manuell analys. Utan detta hänger all
utvärdering på att vi råkar titta.

Mäter ÖVERAVKASTNING mot BTC (coinets rörelse minus marknadens) så en bra vecka
för hela marknaden inte förväxlas med en bra signal. Skickas söndagar.
"""
from datetime import datetime, timedelta, timezone

import pandas as pd

import alerts
import db

LOOKBACK_DAYS = 30
HORIZON_H = 48
REPORT_WEEKDAY = 6      # 6 = söndag
REPORT_HOUR = 17        # UTC (≈19 svensk tid)
STATE_KEY = "last_weekly_report"


def _bucket(meta):
    """Grupperar på det som MÄTBART separerar utfall: trendstyrka och volym.

    Grupperade tidigare på OI, men mätningen 2026-08-30 (n=93) visade att OI ger
    0.2 procentenheters skillnad medan trendstyrka ger 5.1. Att fortsätta
    redovisa OI-grupper hade fortsatt antyda att OI betyder något.
    """
    import re
    import scout
    meta = meta or {}
    eff = re.search(r"eff (\d\.\d+)", meta.get("regime", "") or "")
    b = scout.bucket_of(float(eff.group(1)) if eff else None, meta.get("vol_ratio"))
    return {"stark/hog": "stark trend + hög volym",
            "stark/lag": "stark trend + låg volym",
            "svag/hog": "svag trend + hög volym",
            "svag/lag": "svag trend + låg volym"}[b]


def build(conn, days: int = LOOKBACK_DAYS) -> str:
    rows = db.load_flag_outcomes(conn, days)
    ids = db.load_coin_ids(conn)
    dfs = {}

    def fwd(cid, sym, t0, hrs):
        if sym not in dfs:
            dfs[sym] = db.load_ohlcv_df(conn, cid, "1h")
        df = dfs[sym]
        if df.empty:
            return None
        i0 = df.index.get_indexer([t0], method="nearest")[0]
        t1 = t0 + timedelta(hours=hrs)
        i1 = df.index.get_indexer([t1], method="nearest")[0]
        if abs((df.index[i1] - t1).total_seconds()) > 3 * 3600:
            return None
        return df["close"].iloc[i1] / df["close"].iloc[i0] - 1

    groups = {}
    for sym, cid, ft, ts, meta in rows:
        r = fwd(cid, sym, ts, HORIZON_H)
        m = fwd(ids.get("BTC"), "BTC", ts, HORIZON_H) if ids.get("BTC") else None
        if r is None or m is None:
            continue
        groups.setdefault((ft, _bucket(meta)), []).append(r - m)

    L = [f"📊 <b>VECKORAPPORT</b> — senaste {days} dygnen",
         f"<i>Överavkastning mot BTC {HORIZON_H}h efter varje flagga. "
         f"Positivt = flaggan slog marknaden.</i>"]

    titles = {"turning_up": "🟢 Vänder upp + volym",
              "falling": "🟡 Faller + volym <i>(loggas, skickas ej)</i>",
              "distribution": "🔴 Säljvolym <i>(loggas, skickas ej)</i>"}
    for ft, title in titles.items():
        lines = []
        for b in ("stark trend + hög volym", "stark trend + låg volym",
                  "svag trend + hög volym", "svag trend + låg volym"):
            v = groups.get((ft, b))
            if not v:
                continue
            avg = sum(v) / len(v) * 100
            lines.append(f"  {b:<24}{avg:+5.1f}%  (n={len(v)})")
        if lines:
            L.append(f"\n<b>{title}</b>")
            L.extend(lines)

    # 🔎 Hälsokollen: frågan är inte "slog den BTC" utan "hade jag rätt i att varna".
    # Mäts på ABSOLUT prisrörelse efter larmet — föll priset var varningen befogad,
    # steg det hade du tjänat på att sitta kvar. Det här avgör om den någonsin får
    # bli ett säljlarm (i dag är den ren information, se exit_watch).
    health = db.load_flag_outcomes(conn, days, flag_types=("health",))
    if health:
        moves = []
        for sym, cid, ft, ts, meta in health:
            r = fwd(cid, sym, ts, HORIZON_H)
            if r is not None:
                moves.append(float(r))
        if moves:
            ratt = [m for m in moves if m < 0]
            snitt = sum(moves) / len(moves) * 100
            L.append(f"\n<b>🔎 Hälsokoll på innehav</b>")
            L.append(f"  {len(ratt)} av {len(moves)} gånger föll priset efter varningen "
                     f"({HORIZON_H}h) · snitt {snitt:+.1f}%")
            L.append(f"  <i>Negativt snitt = varningen kom i tid. Positivt = du hade "
                     f"tjänat på att sitta kvar. Vid mätningen 2026-08-30 var det ett "
                     f"nollsummespel (+2,4 procentenheter på 32 trades).</i>")

    closed = db.load_closed_holdings(conn, days)
    if closed:
        pls = [(float(x) / float(e) - 1) * 100 for _, e, x, _, _ in closed if x]
        wins = [p for p in pls if p > 0]
        L.append(f"\n<b>Dina avslutade trades:</b> {len(pls)} st, "
                 f"{len(wins)} plus / {len(pls)-len(wins)} minus, "
                 f"summa {sum(pls):+.1f}%")
        best = max(closed, key=lambda r: float(r[2]) / float(r[1]) if r[2] else 0)
        worst = min(closed, key=lambda r: float(r[2]) / float(r[1]) if r[2] else 9e9)
        if best[2]:
            L.append(f"  bäst {best[0]} {(float(best[2])/float(best[1])-1)*100:+.1f}%"
                     f" · sämst {worst[0]} {(float(worst[2])/float(worst[1])-1)*100:+.1f}%")

    open_h = db.load_open_holdings(conn)
    if open_h:
        parts = []
        for h in open_h:
            p = db.get_last_close(conn, h["coin_id"])
            if p:
                parts.append(f"{h['symbol']} {(p/h['entry']-1)*100:+.0f}%")
        if parts:
            L.append(f"\n<b>Öppna nu:</b> " + " · ".join(parts))

    L.append("\n<i>Små urval — läs som riktning, inte facit. Vänder ett mönster "
             "negativt flera veckor i rad är det en signal att sluta lita på det.</i>")
    return "\n".join(L)


def run(conn, send: bool = True, force: bool = False) -> bool:
    now = datetime.now(timezone.utc)
    if not force:
        if now.weekday() != REPORT_WEEKDAY or now.hour < REPORT_HOUR:
            return False
        last = db.get_bot_state(conn, STATE_KEY)
        if last and (now - datetime.fromisoformat(last)).days < 5:
            return False
    text = build(conn)
    print(text)
    if send:
        alerts.send(text)
        db.set_bot_state(conn, STATE_KEY, now.isoformat())
        print("\n[skickat till Telegram]")
    return True

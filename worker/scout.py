"""Marknads-radar (bevakning, EJ råd).

Flaggar OVANLIGA förhållanden för dig att titta på och bedöma SJÄLV — inga köp/sälj,
inga entry/stop/tp. Det här är den ärliga produkten efter att tre signal-familjer
bevisats sakna edge: boten är din spanare, inte ditt orakel.

Allt från OHLCV (funkar färskt i molnet via Kraken). Hoppar coins med inaktuell data.
"""
from datetime import datetime, timezone

import numpy as np

import alerts
import config
import db
import features
from live_signals import MIN_BARS, _is_stale, _last_closed_idx

TF = "4h"
VOL_BASELINE = 50      # bars för snittvolym
VOL_SPIKE = 2.5        # × snittvolym = ovanligt
FLAT_MOVE = 0.01       # bar-rörelse < 1% → "pris stilla" (volym in, pris reagerar inte)
BIG_MOVE = 0.08        # |24h-rörelse| ≥ 8% = stor
HOT_ATR = 0.90         # ATR-percentil ≥ detta = hög volatilitet
MOM_BARS = 42          # ~7 dygn på 4h


def _snapshot(conn, coin, cid: int, tf: str):
    df = db.load_ohlcv_df(conn, cid, tf)
    if len(df) < MIN_BARS or _is_stale(df, tf):
        return None
    f = features.compute(df)
    i = _last_closed_idx(df, tf)
    vol_base = df["volume"].rolling(VOL_BASELINE).mean().iloc[i]
    atr_pct = f["atr_pctile"].iloc[i]
    return {
        "sym": coin.symbol,
        "vol_ratio": float(df["volume"].iloc[i] / vol_base) if vol_base else 0.0,
        "bar_move": float((df["close"].iloc[i] - df["open"].iloc[i]) / df["open"].iloc[i]),
        "chg24": float(df["close"].iloc[i] / df["close"].iloc[i - 6] - 1),
        "atr_pct": float(atr_pct) if not np.isnan(atr_pct) else 0.0,
        "mom": float(df["close"].iloc[i] / df["close"].iloc[i - MOM_BARS] - 1),
    }


def build_digest(snaps: list) -> tuple:
    vol_spikes = sorted((s for s in snaps if s["vol_ratio"] >= VOL_SPIKE), key=lambda s: -s["vol_ratio"])
    big_moves = sorted((s for s in snaps if abs(s["chg24"]) >= BIG_MOVE), key=lambda s: -abs(s["chg24"]))
    hot = [s for s in snaps if s["atr_pct"] >= HOT_ATR]
    ranked = sorted(snaps, key=lambda s: -s["mom"])
    leaders, laggards = ranked[:3], ranked[-3:][::-1]

    lines = [f"📡 <b>MARKNADS-RADAR</b> (bevakning, ej råd) — {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC\n"]
    if vol_spikes:
        lines.append("🔊 <b>Volym-spikar:</b>")
        for s in vol_spikes[:6]:
            tag = "volym in, pris stilla" if abs(s["bar_move"]) < FLAT_MOVE else f"pris {s['bar_move']*100:+.1f}%"
            lines.append(f"  • {s['sym']}: {s['vol_ratio']:.1f}× snittvolym ({tag})")
    if big_moves:
        lines.append("\n📈 <b>Stora rörelser (24h):</b>")
        lines.append("  " + "   ".join(f"{s['sym']} {s['chg24']*100:+.0f}%" for s in big_moves[:8]))
    if hot:
        lines.append("\n⚡ <b>Hög volatilitet:</b> " + "  ".join(s["sym"] for s in hot[:8]))
    lines.append("\n🏆 <b>Starkast (7d):</b> " + "   ".join(f"{s['sym']} {s['mom']*100:+.0f}%" for s in leaders))
    lines.append("🔻 <b>Svagast (7d):</b> " + "   ".join(f"{s['sym']} {s['mom']*100:+.0f}%" for s in laggards))
    lines.append("\n<i>Observationer att titta på — inte köp/sälj-signaler.</i>")

    has_flags = bool(vol_spikes or big_moves or hot)
    return "\n".join(lines), has_flags


def run(conn, timeframe: str = TF, send: bool = True) -> None:
    coin_ids = db.load_coin_ids(conn)
    snaps = []
    for coin in config.UNIVERSE:
        cid = coin_ids.get(coin.symbol)
        if cid is None:
            continue
        s = _snapshot(conn, coin, cid, timeframe)
        if s:
            snaps.append(s)
    if not snaps:
        print("Ingen färsk data — inget att rapportera.")
        return

    text, has_flags = build_digest(snaps)
    print(text)
    if send and has_flags:
        alerts.send(text)
        print("\n[skickat till Telegram]")
    elif send:
        print("\n[inget ovanligt över trösklarna — inget skickat]")

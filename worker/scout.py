"""Marknads-radar v2 (bevakning, EJ råd).

Flaggar OVANLIGA förhållanden för dig att bedöma SJÄLV — med en neutral förklaring av
vad mönstret OFTA betyder (kunskap, inte rekommendation). Boten är din spanare.

Källor: OHLCV (volym/rörelse/volatilitet/relativ styrka) + derivatives (funding, om
färsk). Funding kräver att ingest-oi körts nyligen (Binance futures → funkar på VPS,
inte på GitHubs US-moln). Saknas/föråldrad funding → hoppas tyst över.
"""
from datetime import datetime, timezone

import numpy as np

import alerts
import config
import db
import features
from live_signals import MIN_BARS, _is_stale, _last_closed_idx

# ---- Trösklar (KÄNSLIGHET — skruva här om den larmar för ofta/sällan) ----
VOL_SPIKE = 3.0          # × snittvolym = ovanlig volym
FLAT_MOVE = 0.005        # bar-rörelse < 0.5% → "pris stilla" (volym in, pris reagerar inte)
BIG_MOVE = 0.06          # |24h-rörelse| ≥ 6% = stor
HOT_ATR = 0.90           # ATR-percentil ≥ detta = hög volatilitet
FUNDING_EXTREME = 0.0003 # |funding| ≥ detta (per intervall) = överhettad positionering
FUNDING_MAX_AGE_H = 6    # funding-data äldre än så ignoreras (ej färsk)

BARS_PER_DAY = {"5m": 288, "15m": 96, "1h": 24, "4h": 6, "1d": 1}

# Neutrala förklaringar — vad mönstret OFTA betyder. INTE ett råd.
EXPLAIN = {
    "vol_flat": "stor volym men priset rör sig knappt — ofta ackumulation/distribution; en del tolkar det som att en rörelse kan vara på väg",
    "vol_move": "volym bakom rörelsen — utbrott med volym anses mer pålitliga än utan",
    "big_up": "kraftig uppgång — ofta nyhets-/momentumdriven; kan bli överköpt och dra tillbaka",
    "big_down": "kraftig nedgång — ofta avveckling/panik; kan bli översåld och studsa",
    "hot": "stora svängningar = högre risk; bredare stop-avstånd brukar behövas",
    "leaders": "starkast mot flocken — momentum-traders följer ledare för fortsatt styrka",
    "laggards": "svagast mot flocken — vissa undviker, andra letar reversal/botten",
    "funding_pos": "extremt positiv funding = longs betalar mycket = överhettade longs; historiskt kopplat till reversal-risk nedåt",
    "funding_neg": "extremt negativ funding = shorts betalar = överhettade shorts; ibland kopplat till squeeze uppåt",
}


def _snapshot(conn, coin, cid: int, tf: str):
    bpd = BARS_PER_DAY[tf]
    df = db.load_ohlcv_df(conn, cid, tf)
    if len(df) < max(MIN_BARS, 7 * bpd + 5) or _is_stale(df, tf):
        return None
    f = features.compute(df)
    i = _last_closed_idx(df, tf)
    vol_base = df["volume"].rolling(2 * bpd).mean().iloc[i]
    atr_pct = f["atr_pctile"].iloc[i]
    return {
        "sym": coin.symbol,
        "vol_ratio": float(df["volume"].iloc[i] / vol_base) if vol_base else 0.0,
        "bar_move": float((df["close"].iloc[i] - df["open"].iloc[i]) / df["open"].iloc[i]),
        "chg24": float(df["close"].iloc[i] / df["close"].iloc[i - bpd] - 1),
        "atr_pct": float(atr_pct) if not np.isnan(atr_pct) else 0.0,
        "mom": float(df["close"].iloc[i] / df["close"].iloc[i - 7 * bpd] - 1),
    }


def _funding_flags(conn) -> list:
    """Färska, extrema funding-rates från derivatives-tabellen."""
    out = []
    now = datetime.now(timezone.utc)
    for sym, funding, ts in db.load_latest_funding(conn):
        if funding is None or ts is None:
            continue
        age_h = (now - ts).total_seconds() / 3600
        if age_h <= FUNDING_MAX_AGE_H and abs(funding) >= FUNDING_EXTREME:
            out.append((sym, float(funding)))
    return sorted(out, key=lambda x: -abs(x[1]))


def build_digest(snaps: list, funding: list, tf: str) -> tuple:
    vol_spikes = sorted((s for s in snaps if s["vol_ratio"] >= VOL_SPIKE), key=lambda s: -s["vol_ratio"])
    big_moves = sorted((s for s in snaps if abs(s["chg24"]) >= BIG_MOVE), key=lambda s: -abs(s["chg24"]))
    hot = [s for s in snaps if s["atr_pct"] >= HOT_ATR]
    ranked = sorted(snaps, key=lambda s: -s["mom"])
    leaders, laggards = ranked[:3], ranked[-3:][::-1]

    L = [f"📡 <b>MARKNADS-RADAR</b> ({tf}, bevakning – ej råd) — {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC\n"]

    if vol_spikes:
        L.append("🔊 <b>Volym-spikar:</b>")
        flat_seen = move_seen = False
        for s in vol_spikes[:6]:
            flat = abs(s["bar_move"]) < FLAT_MOVE
            tag = "volym in, pris stilla" if flat else f"pris {s['bar_move']*100:+.1f}%"
            L.append(f"  • {s['sym']}: {s['vol_ratio']:.1f}× snittvolym ({tag})")
            flat_seen |= flat
            move_seen |= not flat
        if flat_seen:
            L.append(f"  <i>↳ {EXPLAIN['vol_flat']}</i>")
        if move_seen:
            L.append(f"  <i>↳ {EXPLAIN['vol_move']}</i>")

    if big_moves:
        L.append("\n📈 <b>Stora rörelser (24h):</b>")
        L.append("  " + "   ".join(f"{s['sym']} {s['chg24']*100:+.0f}%" for s in big_moves[:8]))
        ups = any(s["chg24"] > 0 for s in big_moves)
        downs = any(s["chg24"] < 0 for s in big_moves)
        if ups:
            L.append(f"  <i>↳ {EXPLAIN['big_up']}</i>")
        if downs:
            L.append(f"  <i>↳ {EXPLAIN['big_down']}</i>")

    if hot:
        L.append("\n⚡ <b>Hög volatilitet:</b> " + "  ".join(s["sym"] for s in hot[:8]))
        L.append(f"  <i>↳ {EXPLAIN['hot']}</i>")

    if funding:
        L.append("\n💰 <b>Funding-extremer:</b>")
        L.append("  " + "   ".join(f"{sym} {fr*100:+.3f}%" for sym, fr in funding[:8]))
        if any(fr > 0 for _, fr in funding):
            L.append(f"  <i>↳ {EXPLAIN['funding_pos']}</i>")
        if any(fr < 0 for _, fr in funding):
            L.append(f"  <i>↳ {EXPLAIN['funding_neg']}</i>")

    L.append("\n🏆 <b>Starkast (7d):</b> " + "   ".join(f"{s['sym']} {s['mom']*100:+.0f}%" for s in leaders))
    L.append("🔻 <b>Svagast (7d):</b> " + "   ".join(f"{s['sym']} {s['mom']*100:+.0f}%" for s in laggards))
    L.append(f"<i>↳ {EXPLAIN['leaders']}</i>")
    L.append("\n<i>Observationer att titta på — inte köp/sälj-signaler.</i>")

    has_flags = bool(vol_spikes or big_moves or hot or funding)
    return "\n".join(L), has_flags


def run(conn, timeframe: str = "1h", send: bool = True) -> None:
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

    funding = _funding_flags(conn)
    text, has_flags = build_digest(snaps, funding, timeframe)
    print(text)
    if send and has_flags:
        alerts.send(text)
        print("\n[skickat till Telegram]")
    elif send:
        print("\n[inget ovanligt över trösklarna — inget skickat]")

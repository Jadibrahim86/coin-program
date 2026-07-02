"""Volym-radar (bevakning, EJ råd) — tre volym-mönster, separat märkta.

  🟢 Vänder UPP + volym   → coinet har börjat röra sig upp och volym bekräftar (AVAX-mönstret)
  🟡 Faller + volym       → volym medan det fortfarande faller (ofta FALLANDE KNIV — riskabelt)
  🔴 Säljvolym efter uppgång → coinet steg, vänder ner med volym (möjlig distribution/topp)

Skillnaden 🟢 vs 🟡 = KORT-momentum (har det vänt upp, eller faller det än). Det var precis
det som skiljde AVAX (vände upp, +10%) från DOT (föll vidare, kniv) i din verkliga data.

Ärligt: även 🟢 missar mer än den träffar — strålkastare att GRANSKA SJÄLV, inte autoköp.
Dedup hindrar upprepning av samma coin+mönster inom DEDUP_HOURS.
"""
from datetime import datetime, timezone

import numpy as np

import alerts
import config
import db
import features
from live_signals import MIN_BARS, _is_stale, _last_closed_idx

# ---- Trösklar (känslighet — skruva här) ----
VOL_SPIKE = 5.0          # × snittvolym = ovanlig volym (~3–4 flaggor/dag; sänk för fler)
TURN_UP = 0.02           # kort-momentum (12h) ≥ +2% = "vänder upp"
TURN_DN = -0.02          # kort-momentum (12h) ≤ -2% = "vänder ner"
TREND_MOVE = 0.04        # ±4% över 5 dygn = "har fallit / har stigit"
OVEREXTENDED = 0.20      # hoppa 🟢 om redan upp >20% på 5d (för sent)
FUNDING_EXTREME = 0.0003
FUNDING_MAX_AGE_H = 6
DEDUP_HOURS = 8

BARS_PER_DAY = {"5m": 288, "15m": 96, "1h": 24, "4h": 6, "1d": 1}

SECTIONS = {
    "turning_up": ("🟢", "<b>Vänder upp + volym</b> (start på rörelse?)",
                   "coinet har börjat röra sig upp och volym bekräftar — mönstret som funkade (AVAX). Granska chart själv."),
    "falling": ("🟡", "<b>Faller + volym</b> (botten? ofta KNIV – var försiktig)",
                "volym medan det fortfarande faller — fångar ofta fallande knivar. Vänta hellre på vändning än att fånga."),
    "distribution": ("🔴", "<b>Säljvolym efter uppgång</b> (möjlig distribution – topp?)",
                     "säljare kliver in efter en uppgång — överväg att säkra vinst."),
}


def _snapshot(conn, coin, cid: int, tf: str):
    bpd = BARS_PER_DAY[tf]
    df = db.load_ohlcv_df(conn, cid, tf)
    if len(df) < max(MIN_BARS, 5 * bpd + 5) or _is_stale(df, tf):
        return None
    i = _last_closed_idx(df, tf)
    c = float(df["close"].iloc[i])
    vol_base = df["volume"].rolling(2 * bpd).mean().iloc[i]
    return {
        "cid": cid, "sym": coin.symbol, "price": c,
        "vol_ratio": float(df["volume"].iloc[i] / vol_base) if vol_base else 0.0,
        "mom_short": float(c / df["close"].iloc[i - 12] - 1),   # ~12h: vänder upp/ner?
        "mom5": float(c / df["close"].iloc[i - 5 * bpd] - 1),   # 5d kontext
    }


def classify(s: dict) -> str | None:
    if s["vol_ratio"] < VOL_SPIKE:
        return None
    if s["mom_short"] >= TURN_UP and s["mom5"] < OVEREXTENDED:
        return "turning_up"
    if s["mom5"] <= -TREND_MOVE and s["mom_short"] <= 0:
        return "falling"
    if s["mom5"] >= TREND_MOVE and s["mom_short"] <= TURN_DN:
        return "distribution"
    return None


def _funding_flags(conn) -> list:
    out, now = [], datetime.now(timezone.utc)
    for sym, funding, ts in db.load_latest_funding(conn):
        if funding is None or ts is None:
            continue
        if (now - ts).total_seconds() / 3600 <= FUNDING_MAX_AGE_H and abs(funding) >= FUNDING_EXTREME:
            out.append((sym, float(funding)))
    return sorted(out, key=lambda x: -abs(x[1]))


def run(conn, timeframe: str = "1h", send: bool = True) -> None:
    coin_ids = db.load_coin_ids(conn)
    buckets = {"turning_up": [], "falling": [], "distribution": []}
    for coin in config.UNIVERSE:
        cid = coin_ids.get(coin.symbol)
        if cid is None:
            continue
        s = _snapshot(conn, coin, cid, timeframe)
        if not s:
            continue
        kind = classify(s)
        if kind:
            buckets[kind].append(s)

    recent = db.recent_radar_alerts(conn, DEDUP_HOURS)
    for k in buckets:
        buckets[k] = sorted((s for s in buckets[k] if (s["cid"], k) not in recent),
                            key=lambda s: -s["vol_ratio"])
    # Dedup funding också — kronisk extrem funding (t.ex. INJ) ska inte upprepas varje timme.
    funding = [(sym, fr) for sym, fr in _funding_flags(conn)
               if (coin_ids.get(sym), "funding") not in recent]

    if not (any(buckets.values()) or funding):
        print("Inget nytt över trösklarna — inget skickat.")
        return

    L = [f"📡 <b>VOLYM-RADAR</b> ({timeframe}, bevakning – ej råd) — {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC"]
    for k in ("turning_up", "falling", "distribution"):
        if not buckets[k]:
            continue
        icon, title, note = SECTIONS[k]
        L.append(f"\n{icon} {title}:")
        for s in buckets[k][:6]:
            L.append(f"  • {s['sym']} ~{s['price']:g}: {s['vol_ratio']:.1f}× volym, 12h {s['mom_short']*100:+.0f}%, 5d {s['mom5']*100:+.0f}%")
        L.append(f"  <i>↳ {note}</i>")
    if funding:
        L.append("\n💰 <b>Funding-extremer:</b> " + "   ".join(f"{sym} {fr*100:+.3f}%" for sym, fr in funding[:8]))
    L.append("\n<i>Strålkastare att granska själv — inte köp/sälj. Fler missar än träffar; din bedömning avgör.</i>")
    text = "\n".join(L)

    print(text)
    if send:
        alerts.send(text)
        db.record_radar_alerts(
            conn,
            [(s["cid"], k) for k in buckets for s in buckets[k]]
            + [(coin_ids[sym], "funding") for sym, _ in funding if sym in coin_ids],
        )
        print("\n[skickat + flaggor registrerade för dedup]")

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
VOL_SPIKE = 6.0          # × snittvolym (höjd 5→6 vid 28-coin-universumet, håller larmvolymen nere)
TURN_UP = 0.015          # kort-momentum (6h) ≥ +1.5% = "vänder upp" (6h-fönster = tidigare upptäckt)
TURN_DN = -0.015         # kort-momentum (6h) ≤ -1.5% = "vänder ner"
LATE_24H = 0.05          # 24h-rörelse > +5% ⇒ "⚠️ sent i rörelsen" på 🟢-flaggor
TREND_MOVE = 0.04        # ±4% över 5 dygn = "har fallit / har stigit"
OVEREXTENDED = 0.20      # hoppa 🟢 om redan upp >20% på 5d (för sent)
FUNDING_EXTREME = 0.0003
FUNDING_MAX_AGE_H = 6
DEDUP_HOURS = 8
FUNDING_DEDUP_HOURS = 24  # kronisk extrem funding (t.ex. INJ) → max en alert per dygn

# --- Marknadsregim (filtrerar 🟢) --------------------------------------------
# Mätning 2026-07-25: 🟢-flaggor gav -1.4%/24h i en platt vecka. Uppdelat på
# marknadens efficiency ratio (trend vs chop) blev det -0.03% i trendande läge
# mot -2.5% i chop. Utbrottssignaler i hackig marknad = köpa toppen av en wiggle.
# OBS: kalibrerat på FÅ observationer — tröskeln hålls trubbig med flit och
# regimen skrivs alltid ut i meddelandet så vi kan fortsätta mäta.
REGIME_REF = "BTC"          # marknadens taktpinne
REGIME_WINDOW = 24          # timmar bakåt för efficiency/förändring
CHOP_MAX = 0.20             # efficiency under detta = chop → inga 🟢
MARKET_DOWN = -0.02         # BTC 24h under detta = risk-off → inga 🟢

# --- Open interest (konfluens) -----------------------------------------------
# OI läst TILLSAMMANS med priset skiljer äkta nya pengar från kulisser:
#   pris upp + OI upp  = nya positioner öppnas → rörelsen har bränsle
#   pris upp + OI ner  = shorts som täcker → ihålig rusning, rinner ofta ut
#   pris ner + OI upp  = nya shorts pressar → nedtrycket har kraft
#   pris ner + OI ner  = longs likvideras → kan närma sig utbottning
# Tröskeln ±2% är satt från fördelningen av 12060 mätta 24h-förändringar
# (kvartiler ±3%) → ~30% "stiger", ~34% "faller", ~36% neutralt.
# EJ VALIDERAD som edge än — visas som markering, mäts på kommande trades.
OI_WINDOW_H = 24
OI_THRESHOLD = 0.02

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
        "mom_short": float(c / df["close"].iloc[i - 6] - 1),    # ~6h: vänder upp/ner? (tidig)
        "mom24": float(c / df["close"].iloc[i - bpd] - 1),      # 24h: hur sen är du?
        "mom5": float(c / df["close"].iloc[i - 5 * bpd] - 1),   # 5d kontext
        "oi_chg": db.oi_change(conn, cid, OI_WINDOW_H),         # derivat-konfluens
    }


def oi_label(kind: str, oi) -> tuple:
    """(markering, kort_text) för ett mönster givet OI-förändringen. Se OI_-kommentaren."""
    if oi is None:
        return "", "OI saknas"
    pct = f"{oi*100:+.0f}%"
    if kind == "turning_up":
        if oi >= OI_THRESHOLD:
            return "✅", f"OI {pct} — nya pengar in"
        if oi <= -OI_THRESHOLD:
            return "⚠️", f"OI {pct} — mest short-covering"
        return "➖", f"OI {pct} — ingen bekräftelse"
    if kind == "falling":
        if oi >= OI_THRESHOLD:
            return "⚠️", f"OI {pct} — nya shorts pressar"
        if oi <= -OI_THRESHOLD:
            return "👀", f"OI {pct} — longs likvideras, kan bottna"
        return "➖", f"OI {pct}"
    if kind == "distribution":
        if oi >= OI_THRESHOLD:
            return "⚠️", f"OI {pct} — nya shorts kliver in"
        if oi <= -OI_THRESHOLD:
            return "➖", f"OI {pct} — longs stänger"
        return "➖", f"OI {pct}"
    return "", f"OI {pct}"


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


def market_regime(conn, coin_ids: dict) -> dict:
    """Marknadens läge just nu: trendande, chop eller risk-off.

    Returnerar {'label', 'allow_long', 'eff', 'chg'} — allow_long=False betyder
    att 🟢-flaggor tystas (de har historiskt failat i chop/nedgång).
    """
    cid = coin_ids.get(REGIME_REF)
    closes = db.load_recent_closes(conn, cid, "1h", REGIME_WINDOW + 1) if cid else None
    if not closes or len(closes) < REGIME_WINDOW:
        return {"label": "okänd", "allow_long": True, "eff": None, "chg": None}

    eff = features.market_efficiency(closes)
    chg = closes[-1] / closes[0] - 1
    if chg <= MARKET_DOWN:
        return {"label": f"risk-off ({REGIME_REF} {chg*100:+.1f}% 24h)", "allow_long": False, "eff": eff, "chg": chg}
    if eff is not None and eff < CHOP_MAX:
        return {"label": f"hackig/chop (eff {eff:.2f})", "allow_long": False, "eff": eff, "chg": chg}
    return {"label": f"trendande (eff {eff:.2f}, {REGIME_REF} {chg*100:+.1f}%)", "allow_long": True, "eff": eff, "chg": chg}


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

    # Marknadsfilter: 🟢 tystas i chop/risk-off (se REGIME-kommentaren ovan).
    regime = market_regime(conn, coin_ids)
    suppressed = 0
    if not regime["allow_long"]:
        suppressed = len(buckets["turning_up"])
        buckets["turning_up"] = []

    recent = db.recent_radar_alerts(conn, DEDUP_HOURS)
    for k in buckets:
        buckets[k] = sorted((s for s in buckets[k] if (s["cid"], k) not in recent),
                            key=lambda s: -s["vol_ratio"])
    # Dedup funding med eget, längre fönster — kronisk extrem funding (t.ex. INJ)
    # ska inte upprepas varje timme, max en gång per dygn.
    recent_funding = db.recent_radar_alerts(conn, FUNDING_DEDUP_HOURS)
    funding = [(sym, fr) for sym, fr in _funding_flags(conn)
               if (coin_ids.get(sym), "funding") not in recent_funding]

    if not (any(buckets.values()) or funding):
        extra = f" ({suppressed} 🟢 tystade — {regime['label']})" if suppressed else ""
        print(f"Inget nytt över trösklarna — inget skickat.{extra}")
        return

    L = [f"📡 <b>VOLYM-RADAR</b> ({timeframe}, bevakning – ej råd) — {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC",
         f"<i>Marknad: {regime['label']}</i>"]
    if suppressed:
        L.append(f"<i>({suppressed} köp-flagga(or) tystad — köpsignaler har historiskt "
                 f"failat i det här marknadsläget)</i>")
    for k in ("turning_up", "falling", "distribution"):
        if not buckets[k]:
            continue
        icon, title, note = SECTIONS[k]
        L.append(f"\n{icon} {title}:")
        for s in buckets[k][:6]:
            late = "  ⚠️ sent i rörelsen" if k == "turning_up" and s["mom24"] > LATE_24H else ""
            mark, oitxt = oi_label(k, s["oi_chg"])
            L.append(f"  • {mark} <b>{s['sym']}</b> ~{s['price']:g}: {s['vol_ratio']:.1f}× volym, "
                     f"6h {s['mom_short']*100:+.0f}%, 24h {s['mom24']*100:+.0f}%, "
                     f"5d {s['mom5']*100:+.0f}%{late}\n"
                     f"      {oitxt}")
        L.append(f"  <i>↳ {note}</i>")
        if k == "turning_up":
            L.append("  <i>↳ ✅ = pris + volym + OI drar åt samma håll (konfluens). "
                     "⚠️ = uppgången drivs av short-covering och rinner ofta ut. "
                     "OI-delen är ny och ovaliderad — vi mäter den på kommande trades.</i>")
    if funding:
        L.append("\n💰 <b>Funding-extremer:</b> " + "   ".join(f"{sym} {fr*100:+.3f}%" for sym, fr in funding[:8]))
    L.append("\n<i>Strålkastare att granska själv — inte köp/sälj. Fler missar än träffar; din bedömning avgör.</i>")
    text = "\n".join(L)

    print(text)
    if send:
        alerts.send(text)
        db.record_radar_alerts(
            conn,
            [(s["cid"], k, {"price": s["price"], "vol_ratio": round(s["vol_ratio"], 1),
                            "oi_chg": s["oi_chg"], "mom24": s["mom24"],
                            "regime": regime["label"]})
             for k in buckets for s in buckets[k]]
            + [(coin_ids[sym], "funding", {"funding": fr})
               for sym, fr in funding if sym in coin_ids],
        )
        print("\n[skickat + flaggor registrerade för dedup]")

"""Marknadsstress-larm: varnar när marknaden beter sig extremt eller onormalt,
med fokus på hur DINA innehav påverkas.

Bakgrund: 27-28 juli gick alla öppna positioner rött samtidigt — inte för att
coin-valen var dåliga utan för att hela marknaden vände i lockstep. Då är
"diversifiering" en illusion och det enda som hjälper är att veta om det.

Fyra oberoende mått, alla kalibrerade mot 45 dygns egen historik (percentiler
inom parentes) så larmet blir sällsynt:
  • BTC faller snabbt        (6h ≤ -1.2%   = 5:e percentilen)
  • Brett fall               (≥ 65% av coinsen faller = 95:e)
  • Allt rör sig ihop        (snittkorrelation ≥ 0.61 = 90:e)
  • Vilda rörelser           (tvärsnittsvolatilitet ≥ 0.65% = 95:e)
Plus likvidationskaskad via aggregerad OI. Larmet går när minst 2 slår till.
"""
from datetime import datetime, timezone

import numpy as np
import pandas as pd

import alerts
import config
import db

BTC_DROP = -0.012        # BTC 6h-rörelse
BREADTH_MAX = 0.65       # andel coins som faller (6h-snitt)
CORR_MAX = 0.61          # snittkorrelation, 48h
XVOL_MAX = 0.0065        # tvärsnittsvolatilitet, 6h-snitt
OI_CASCADE = -0.04       # aggregerad OI-förändring 24h = masslikvidering
MIN_HITS = 2             # så många mått måste slå till
DEDUP_HOURS = 12
CORR_WINDOW = 48


def load_panel(conn, hours: int = 200) -> pd.DataFrame:
    """Stängningskurser för hela universumet, en kolumn per coin."""
    ids = db.load_coin_ids(conn)
    cols = {}
    for coin in config.UNIVERSE:
        cid = ids.get(coin.symbol)
        if not cid:
            continue
        closes = db.load_recent_closes(conn, cid, "1h", hours)
        if len(closes) >= hours * 0.8:
            cols[coin.symbol] = pd.Series(closes)
    if not cols:
        return pd.DataFrame()
    return pd.DataFrame(cols).dropna()


def measure(panel: pd.DataFrame, agg_oi_chg=None) -> dict:
    """Räknar ut de fem stressmåtten på panelens SISTA rad."""
    if panel.empty or len(panel) < CORR_WINDOW + 6 or "BTC" not in panel:
        return {}
    rets = panel.pct_change().dropna()
    tail = rets.tail(CORR_WINDOW)
    c = tail.corr().values
    iu = np.triu_indices_from(c, k=1)
    out = {
        "btc6": float(panel["BTC"].iloc[-1] / panel["BTC"].iloc[-7] - 1),
        "breadth": float((rets.tail(6) < 0).mean(axis=1).mean()),
        "corr": float(np.nanmean(c[iu])),
        "xvol": float(rets.tail(6).std(axis=1).mean()),
        "agg_oi": agg_oi_chg,
    }
    hits = []
    if out["btc6"] <= BTC_DROP:
        hits.append(f"BTC {out['btc6']*100:+.1f}% på 6h — marknaden faller snabbt")
    if out["breadth"] >= BREADTH_MAX:
        hits.append(f"{out['breadth']*100:.0f}% av coinsen faller — brett fall, inte coin-specifikt")
    if out["corr"] >= CORR_MAX:
        hits.append(f"snittkorrelation {out['corr']:.2f} — allt rör sig ihop, diversifiering hjälper inte nu")
    if out["xvol"] >= XVOL_MAX:
        hits.append(f"ovanligt vilda rörelser (tvärsnittsvol {out['xvol']*100:.2f}%)")
    if agg_oi_chg is not None and agg_oi_chg <= OI_CASCADE:
        hits.append(f"aggregerad OI {agg_oi_chg*100:+.0f}% — masslikvidering pågår")
    out["hits"] = hits
    return out


def effective_positions(n: int, korr) -> float | None:
    """Hur många OBEROENDE innehav dina n positioner egentligen motsvarar.

    n / (1 + (n-1)·korrelation) — standardmåttet för effektiv diversifiering.
    Med 6 positioner: korrelation 0.26 (vanlig dag) ger 2.6 oberoende innehav,
    0.68 (23 aug 2026) ger 1.4. Samma pengar, helt olika risk.

    Poängen är att säga något KONKRET utan att låtsas veta riktningen — mätningen
    2026-08-30 visade att stresslarmet inte förutsäger upp eller ner alls
    (snitt -0.07% på 24h, 6 av 11 negativa). Det det däremot vet är att
    positionerna inte längre är oberoende, och det är en storleksfråga.
    """
    if n <= 0 or korr is None:
        return None
    d = 1 + (n - 1) * max(0.0, min(1.0, korr))
    return n / d if d > 0 else None


def run(conn, send: bool = True) -> int:
    panel = load_panel(conn)
    m = measure(panel, db.aggregate_oi_change(conn, 24))
    if not m or len(m["hits"]) < MIN_HITS:
        print("Ingen marknadsstress över trösklarna.")
        return 0

    ids = db.load_coin_ids(conn)
    btc_id = ids.get("BTC")
    if btc_id and (btc_id, "stress") in db.recent_radar_alerts(conn, DEDUP_HOURS):
        print("Marknadsstress redan larmad senaste 12h — tyst.")
        return 0

    L = [f"🌩️ <b>MARKNADSLARM</b> — {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC\n",
         "Marknaden beter sig ovanligt just nu:"]
    for h in m["hits"]:
        L.append(f"  • {h}")

    holdings = db.load_open_holdings(conn)
    if holdings:
        n_eff = effective_positions(len(holdings), m["corr"])
        if n_eff is not None:
            L.append(f"\n⚖️ <b>Dina {len(holdings)} positioner beter sig som "
                     f"{n_eff:.1f} oberoende innehav</b> just nu.")
            L.append("<i>Samma pengar, men risken klumpar ihop sig — som om du satsat "
                     f"allt på {n_eff:.1f} coin i stället för {len(holdings)}.</i>")
        L.append("\n<b>Dina innehav:</b>")
        for h in holdings:
            price = db.get_last_close(conn, h["coin_id"])
            if not price:
                continue
            pl = (price / h["entry"] - 1) * 100
            to_stop = (price / h["stop"] - 1) * 100 if h["stop"] else None
            near = "  ⚠️ nära stoppen" if to_stop is not None and to_stop < 3 else ""
            L.append(f"  • {h['symbol']}: {pl:+.1f}% sedan köp"
                     + (f" · {to_stop:+.1f}% till stoppen" if to_stop is not None else "") + near)
        L.append("\n<i>I det här läget rör sig dina positioner mest med marknaden, "
                 "inte av egna skäl. Vänta hellre än att köpa mer — och kom ihåg att "
                 "flera alts samtidigt är ETT bet, inte flera.</i>")
    else:
        L.append("\n<i>Du har inga öppna positioner — bra läge att avvakta snarare än "
                 "att leta ingångar.</i>")

    text = "\n".join(L)
    print(text)
    if send:
        alerts.send(text)
        if btc_id:
            db.record_radar_alerts(conn, [(btc_id, "stress", {k: m[k] for k in
                                          ("btc6", "breadth", "corr", "xvol", "agg_oi")})])
        print("\n[skickat till Telegram]")
    return len(m["hits"])

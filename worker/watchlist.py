"""👁 Bevakningslistan — coins du följer utan att äga (/bevaka XRP).

SKILLNADEN MOT ALLA ANDRA LARM I SYSTEMET: de triggar på att ett värde passerar
en tröskel. Det här triggar på att coinets TILLSTÅND har flyttat sig sedan
förra rapporten. Prisrörelse, volym, open interest, riktning och läge i
intervallet delas in i grova lägen, och du hörs av när något av dem byter läge.

Varför just så, och inte som ett bottenlarm: mätningen 2026-09-19 gick igenom
43 153 timmar mitt i ras över 41 coins och testade de fyra tecken som brukar
kallas botten — avtagande säljvolym, OI som slutar falla, högre botten, momentum
som vänder. **Inget av dem separerade utfallet**, och varje grupp gick i snitt
6–8% längre ner. Det finns alltså ingen observerbar punkt där man kan säga att
ett fall är över.

Därför påstår den här modulen ingenting om vart coinet ska. Den säger vad som
har ändrats, och låter dig döma. Att beskriva ett tillstånd kan vi göra
pålitligt; att förutsäga en vändning kan vi bevisligen inte.

Allt loggas till radar_alerts ('watch') så vi om några månader kan mäta om
bevakade coins faktiskt gick bättre — och om något av lägesbytena visar sig
förutsäga något, då har vi underlag att bygga ett riktigt tecken på.
"""
from datetime import datetime, timezone

import numpy as np

import alerts
import db
import features
import scout
from live_signals import _is_stale, _last_closed_idx

MIN_TIMMAR_MELLAN = 8        # aldrig mer än var åttonde timme per coin
MIN_PRIS_ANDRING = 0.05      # prisläget byts först vid 5% rörelse sedan senast
TROGHET = 0.30               # hur långt in i nästa läge värdet måste ta sig

# Lägesindelningar. GROVA med flit. Första versionen hade fem lägen per mått med
# fasta gränser, och gav 5.5 rapporter per coin och dygn — riktningen darrade
# över gränsen 814 gånger på 12 coins under tre veckor. Två ändringar fixade det:
#
#   1. Färre lägen (tre istället för fem där det gick).
#   2. TRÖGHET: värdet måste ta sig en bit IN i det nya läget innan bytet
#      räknas, annars sitter man kvar. Utan det rapporterar varje coin varje
#      gång det studsar på en gräns.
#
# Riktningen skalas dessutom mot coinets egen dagsvolatilitet — 1% betyder
# något helt annat för BTC än för ETHFI. Samma princip som stoppen använder.
RIKTNING_MULT = [
    (-9.0, -0.5, "faller"),
    (-0.5, 0.5, "står stilla"),
    (0.5, 9.0, "stiger"),
]
VOLYM = [
    (0.0, 0.5, "nästan ingen handel"),
    (0.5, 2.0, "normal handel"),
    (2.0, 6.0, "förhöjd handel"),
    (6.0, 1e9, "volymspik"),
]
OI = [
    (-9.0, -0.02, "pengar lämnar"),
    (-0.02, 0.02, "oförändrat"),
    (0.02, 9.0, "nya pengar in"),
]


def _lage(tabell, varde, gammalt: str | None = None):
    """Vilket läge värdet hamnar i — med tröghet mot det gamla läget.

    Sitter man redan i ett läge krävs att värdet tar sig TROGHET av nästa
    läges bredd förbi gränsen innan bytet räknas.
    """
    if varde is None:
        return None
    traff = next((n for lo, hi, n in tabell if lo <= varde < hi), tabell[-1][2])
    if gammalt is None or traff == gammalt:
        return traff
    for lo, hi, namn in tabell:
        if namn != gammalt:
            continue
        bredd = min(hi - lo, 1e6)
        marginal = TROGHET * bredd
        if lo - marginal <= varde < hi + marginal:
            return gammalt          # kvar i gamla läget, för nära gränsen
    return traff


def las_tillstand(conn, w: dict, timeframe: str = "1h") -> dict | None:
    """Allt vi kan observera om coinet just nu."""
    df = db.load_ohlcv_df(conn, w["coin_id"], timeframe)
    if len(df) < 60 or _is_stale(df, timeframe):
        return None
    i = _last_closed_idx(df, timeframe)
    pos = len(df) + i if i < 0 else i
    c = float(df["close"].iloc[pos])

    volbas = df["volume"].rolling(48).mean().iloc[pos]
    vol6 = float(df["volume"].iloc[max(0, pos - 5):pos + 1].mean())
    volkvot = vol6 / float(volbas) if volbas else None

    mom6 = float(c / df["close"].iloc[pos - 6] - 1)
    mom24 = float(c / df["close"].iloc[pos - 24] - 1) if pos >= 24 else None
    oi = db.oi_since(conn, w["coin_id"], w["started_at"])
    oi24 = db.oi_change(conn, w["coin_id"], 24)

    # Riktningen mäts i coinets egna mått: ett 1%-hopp är brus för ETHFI och en
    # händelse för BTC. Dagsvol delat på 4 ~ typisk 6h-rörelse.
    dagsvol = features.daily_vol(df["close"].iloc[max(0, pos - 240):pos + 1].tolist())
    ref = (dagsvol / 4) if dagsvol else 0.01
    gam = w.get("last_state") or {}

    # Läge i intervallet sedan bevakningen började
    sedan = df["close"].iloc[max(0, pos - 24 * 14):pos + 1]
    if w["start_price"]:
        sedan = df["close"][df.index >= w["started_at"]]
    lag = float(sedan.min()) if len(sedan) else c
    hog = float(sedan.max()) if len(sedan) else c
    spann = (hog - lag) or 1e-9
    plats = (c - lag) / spann

    if plats < 0.25:
        plats_namn = "nära botten"
    elif plats > 0.75:
        plats_namn = "nära toppen"
    else:
        plats_namn = "mitt i spannet"
    # tröghet även på läget i spannet
    if gam.get("plats") and abs(plats - (0.25 if gam["plats"] == "nära botten"
                                         else 0.75 if gam["plats"] == "nära toppen"
                                         else plats)) < 0.08:
        plats_namn = gam["plats"]

    return {
        "pris": c,
        "sedan_start": (c / w["start_price"] - 1) if w["start_price"] else None,
        "riktning": _lage(RIKTNING_MULT, mom6 / ref if ref else None, gam.get("riktning")),
        "volym": _lage(VOLYM, volkvot, gam.get("volym")),
        "oi": _lage(OI, oi24, gam.get("oi")),
        "plats": plats_namn,
        # råvärden för utskrift och loggning
        "_mom6": mom6, "_mom24": mom24, "_volkvot": volkvot,
        "_oi_sedan": oi, "_oi24": oi24, "_lag": lag, "_hog": hog,
    }


LAGEN = ("riktning", "volym", "oi", "plats")


def vad_andrades(gammalt: dict | None, nytt: dict) -> list:
    """[(vad, från, till)] för varje läge som bytt sedan senaste rapporten."""
    if not gammalt:
        return []
    ut = []
    for nyckel in LAGEN:
        a, b = gammalt.get(nyckel), nytt.get(nyckel)
        if a and b and a != b:
            ut.append((nyckel, a, b))
    # Priset räknas som ändrat först vid en rejäl rörelse, annars skulle varje
    # coin rapportera varje gång det rör sig en halv procent.
    pa, pb = gammalt.get("pris"), nytt.get("pris")
    if pa and pb and abs(pb / pa - 1) >= MIN_PRIS_ANDRING:
        ut.append(("pris", f"{pa:g}", f"{pb:g}"))
    return ut


ETIKETT = {"riktning": "Riktningen", "volym": "Handeln", "oi": "Derivaten",
           "plats": "Läget", "pris": "Priset"}


def _pct(v) -> str:
    """Procent med en decimal nära noll. "-0%" har lurat oss tre gånger förut."""
    if v is None:
        return "okänt"
    return f"{v*100:+.1f}%" if abs(v) < 0.10 else f"{v*100:+.0f}%"


def _lista(ord_: list) -> str:
    """["a","b","c"] -> "a, b och c" — svensk uppräkning, inte kommaräcka."""
    if len(ord_) == 1:
        return ord_[0]
    return ", ".join(ord_[:-1]) + " och " + ord_[-1]


def bygg_rapport(w: dict, s: dict, andringar: list, forsta: bool) -> str:
    dagar = (datetime.now(timezone.utc) - w["started_at"]).total_seconds() / 86400
    L = []
    if forsta:
        L.append(f"👁 <b>{w['symbol']}: bevakning påbörjad</b>")
    else:
        rubrik = _lista([ETIKETT[k].lower() for k, _, _ in andringar])
        L.append(f"👁 <b>{w['symbol']}: {rubrik} har ändrats</b>")

    if s["sedan_start"] is not None:
        L.append(f"  {s['pris']:g} · {s['sedan_start']*100:+.1f}% sedan du började "
                 f"bevaka ({dagar:.0f} d)")
    else:
        L.append(f"  {s['pris']:g} · bevakad i {dagar:.0f} d")

    if andringar:
        L.append("")
        for nyckel, fran, till in andringar:
            L.append(f"  🔄 <b>{ETIKETT[nyckel]}:</b> {fran} → <b>{till}</b>")

    L.append("")
    L.append(f"  Just nu: {s['riktning']} · {s['volym']} · {s['oi']} · {s['plats']}")
    detalj = []
    if s["_mom6"] is not None:
        detalj.append(f"6h {s['_mom6']*100:+.1f}%")
    if s["_mom24"] is not None:
        detalj.append(f"24h {s['_mom24']*100:+.1f}%")
    if s["_volkvot"] is not None:
        detalj.append(f"volym {s['_volkvot']:.1f}× normalt")
    if s["_oi24"] is not None:
        detalj.append(f"OI {_pct(s['_oi24'])} på ett dygn")
    if detalj:
        L.append(f"  <i>{' · '.join(detalj)}</i>")
    L.append(f"  <i>Spann sedan start: {s['_lag']:g} – {s['_hog']:g}</i>")

    L.append("")
    if forsta:
        # Hela brasklappen bara vid start. Den är viktig men får inte stå i
        # varje meddelande — samma fel som radarns fotnot hade.
        L.append("  <i>Det här är vad som HAR hänt, inte vart det ska. De fyra "
                 "tecken som brukar kallas botten — avtagande säljvolym, OI som "
                 "slutar falla, högre botten, momentum som vänder — är mätta på "
                 "43 000 timmar mitt i ras. Inget av dem förutsade något, och "
                 "varje grupp gick i snitt 6–8% längre ner. Siffrorna är dina, "
                 "bedömningen är din.</i>")
    else:
        L.append("  <i>Vad som hänt, inte vart det ska — inget av det här "
                 "förutsäger en botten.</i>")
    return "\n".join(L)


def run(conn, timeframe: str = "1h", send: bool = True) -> int:
    db.ensure_exit_tables(conn)
    lista = db.load_watchlist(conn)
    if not lista:
        print("Inga bevakade coins.")
        return 0

    nu = datetime.now(timezone.utc)
    meddelanden = []
    for w in lista:
        s = las_tillstand(conn, w, timeframe)
        if not s:
            print(f"  {w['symbol']}: för lite eller inaktuell data")
            continue
        forsta = w["last_state"] is None
        if not forsta and w["last_report"]:
            if (nu - w["last_report"]).total_seconds() / 3600 < MIN_TIMMAR_MELLAN:
                continue
        andringar = vad_andrades(w["last_state"], s)
        if not forsta and not andringar:
            print(f"  {w['symbol']}: oförändrat läge")
            continue

        meddelanden.append(bygg_rapport(w, s, andringar, forsta))
        if send:
            db.update_watch(conn, w["id"], s)
            db.record_radar_alerts(conn, [(w["coin_id"], "watch", {
                "pris": s["pris"], "sedan_start": s["sedan_start"],
                "riktning": s["riktning"], "volym": s["volym"], "oi": s["oi"],
                "plats": s["plats"], "vol_kvot": s["_volkvot"],
                "oi24": s["_oi24"], "mom24": s["_mom24"],
                "andringar": [k for k, _, _ in andringar],
            })])

    if meddelanden:
        text = "\n\n".join(meddelanden)
        print(text)
        if send:
            alerts.send(text)
            print("\n[skickat till Telegram]")
    return len(meddelanden)

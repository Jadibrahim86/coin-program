"""Exit-vakt: kollar dina bevakade innehav (holdings) varje timme och larmar när
det är läge att sälja. Reaktiv, inte förutsägande — vi försöker INTE pricka toppen,
vi reagerar när rörelsen viker.

Larm per innehav:
  ❌ STOP   — priset bröt din stop (upprepas ~1×/dygn så länge det ligger under)
  📉 TRAIL  — du är FAKTISKT i vinst och priset har vikt ner från toppen
              (bandet skalas mot coinets dagsvolatilitet; åter-aktiveras vid ny topp)
  🔎 HÄLSOKOLL — max 2×/dygn: vinsten rinner tillbaka, marknaden vänder också,
              eller grunden för köpet (OI/volym/mot BTC) håller inte längre
  🔴 SÄLJVOLYM — ovanligt hög volym + vikande momentum i ett coin du äger

(🟠 VINSTEN VÄNDER fanns 17–30 aug 2026 och togs bort — se nedan och CLAUDE.md.)

Kalibrering 2026-07-25 efter en vecka med verklig data: trailen larmade tidigare vid
+2% över entry med 3%-band, vilket kapade vinnare vid ~0% (RAY larmade t.o.m. "säkra
vinst" på -1.1% förlust) medan förluster fick löpa till full stop. Trösklarna nedan
gör larmen symmetriska: vinnare får utrymme, och "säkra vinst" sägs bara i vinst.

TILLÄGG 2026-08-17 — det tidiga larmet (🟠). Problemet användaren beskrev: han låg
några procent plus, positionen vände, gick tillbaka genom ingången och vidare ner
till stoppen — och det ENDA larmet kom vid stop-brottet på ca -7%. Orsaken är att
PROFIT_ARM = 1.06 aldrig nåddes: CHZ, ATOM och TAO toppade under +6%, så trailen
armerades aldrig. Han säger uttryckligen att han hellre tar 3-5% än att se det gå
till minus.

Lösningen är INTE att sänka PROFIT_ARM — det var precis den nivån som kapade
vinnarna i juli. I stället ett eget larm som kräver BEVIS i stället för bara en
prisrörelse: du ligger plus OCH volymen är onormalt hög OCH momentum viker.
Volymkravet är det som skiljer det från juli-larmen, som gick på ren prisrörelse
och därför tjöt på brus.
"""
from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np

import alerts
import db
import features
import scout
from live_signals import _is_stale, _last_closed_idx

TRAIL_MIN = 0.05        # trail-band = max(5%, 1.5 × dagsvolatilitet)
TRAIL_VOL_MULT = 1.5
PROFIT_ARM = 1.06       # trailen aktiveras först när toppen varit ≥ +6% över entry
MIN_PROFIT_NOW = 0.03   # ...och larmar bara om du ÄR i vinst just nu (≥ +3%)
DIST_DEDUP_HOURS = 8    # säljvolym-larm per coin max var 8:e timme
STOP_REMIND_HOURS = 20  # stop-larm upprepas ungefär en gång per dygn

# --- 🔎 Hälsokoll på öppna positioner ----------------------------------------
# ERSÄTTER det tidiga vinstlarmet (🟠) som fanns här 17-30 aug 2026. Det larmet
# utlöstes ALDRIG — inte en enda gång — och en simulering mot alla 15 innehav
# visade att det aldrig hade kunnat utlösas heller. Felet var ett antagande:
# jag krävde hög volym SAMTIDIGT som fallande momentum, men i den här datan är
# de motsatt korrelerade. När priset viker på 6h ligger volymen typiskt UNDER
# snittet (median 0.43×). Villkoren möttes 0 gånger av 1014 timmar i vinst.
#
# Den här kollar i stället om GRUNDEN för köpet finns kvar, flera gånger per
# dygn så länge du äger coinet. Tre oberoende mått, alla mätta sedan DITT köp:
#   1. Läckte de nya pengarna ut igen?  (open interest)
#   2. Dog intresset?                   (volym mot baslinjen)
#   3. Släpar coinet efter marknaden?   (mot BTC sedan köpet)
#
# Stöd i data: flaggor där OI fortsatte upp efter 24h gav +1.9% mot BTC (57%
# positiva, n=40); där OI vände ner gav de +0.5% (48%, n=31). Separationen är
# alltså ÄKTA MEN SVAG — därför formuleras det som "grunden har upphört att
# gälla", inte som en säljorder. Loggas som 'position_health' för utvärdering.
HEALTH_MIN_HOURS = 12       # ingen koll förrän positionen fått ett halvdygn på sig
HEALTH_OI_LEAK = -0.02      # OI ned ≥2% sedan köpet = pengarna lämnade
HEALTH_VOL_DEAD = 0.7       # senaste 12h volym < 70% av baslinjen = intresset dog
HEALTH_LAG_BTC = -0.02      # ≥2 procentenheter sämre än BTC sedan köpet
HEALTH_MIN_HITS = 2         # så många av de tre ovan måste slå till

# --- Vinsten rinner tillbaka (eget larm, räcker ensamt) ----------------------
# ETHFI 10-13 sep är fallet som motiverar den: toppade +11.0%, föll 16.5
# procentenheter till -7.3%, och INGET larm gick. Varför inget av de befintliga
# räckte:
#   stop        ligger på -15% (2.4 × dagsvol 8%) — 9 procentenheter bort ännu
#   trail       kräver band = 1.5 × dagsvol = 12% OCH att du är +3% just då.
#               På ett 8%/dag-coin är bandet bredare än vinsten hinner bli, så
#               när priset fallit 12% från toppen är du redan under +3%. Trailen
#               kan i praktiken aldrig utlösa på såna coins.
#   hälsokoll   OI steg hela tiden (+13% vid slutet), så "pengarna lämnade" var
#               falskt; volym-villkoret var trasigt (se buggen ovan).
#
# Den här tittar bara på DIN vinst och DIN topp. Bandet är 1.0 × dagsvolatilitet
# (mot trailens 1.5) med 4% golv — på ETHFI hade det larmat 13 sep 00:00 medan
# positionen fortfarande låg +2.1%, i stället för tystnad hela vägen till -7.3%.
PEAK_ARM = 1.05             # toppen måste ha varit minst +5% över ditt köp
PEAK_DROP_MIN = 0.04        # band = max(4%, 1.0 × dagsvol)
PEAK_DROP_VOL_MULT = 1.0
HEALTH_DEDUP_HOURS = 12     # → som mest 2 gånger per dygn och coin
#
# VERIFIERAT MOT HISTORIK innan den byggdes (det steget hoppade jag över med 🟠):
# på 32 avslutade trades / 200 innehavsdygn hade den utlöst för 24 av dem, ca 1
# gång per innehav och dygn med 8h-dedup. MEN att sälja på första larmet hade
# gett +2.4 procentenheter totalt — alltså ingen skillnad alls. Den räddade
# förlorarna (OP +13.4, ATOM +7.8, CHZ +6.8) men kapade vinnarna (POL -22.7,
# WLD -20.6, UNI -12.1). Därför är den formulerad som INFORMATION och siffran
# står i utskicket. Om den någonsin ska bli ett säljlarm måste den siffran bli
# tydligt positiv först.
#
# OBS: den mätningen gjordes MED volym-buggen (villkoret kunde aldrig slå till).
# Omgjord 2026-09-23 med rättad kod, PEAK och BTC-villkoret, genom riktiga
# _health(): sälj på första 🔎 slog ditt eget sälj i 18 av 28 trades men gav
# -131 procentenheter totalt (ZEC -60, POL -34, OP -26, WLD -25). Fotnoten
# stod kvar på "+2,4, ingen skillnad" i tio dagar efter att den blivit fel.
# Den är daterad nu, så det syns när den behöver göras om.


# --- Marknaden vänder också (tillägg 2026-09-23, räcker ensamt) --------------
# Användaren: "ligger toppen på +20% och den börjar neråt vill jag hellre sälja
# på +17% än +11%". PEAK-regeln ovan väntar på max(4%, 1 × dagsvol) — på ett
# 8%/dag-coin är det 8 procentenheter, alltså just +20 → +12.
#
# Ett snävare band rakt av förlorade i simuleringen (tusentals köp, 180 dygn):
# vinnarna dippar ofta 3–5% på vägen upp, och att sälja där kapar dem. Men
# coinen rör sig med BTC — när BTC OCKSÅ vänder från sin topp sedan ditt köp är
# dippen oftare början på något större. Kombinerat med PEAK-regeln (den som går
# först): sämre än bara PEAK i 6% av köpen, bättre i 11%, och bättre median.
# Snittet i stort sett oförändrat — det här flyttar larmet TIDIGARE, det gör
# inte strategin bättre. Det är vad användaren bad om: 1% mer hellre än 1% mindre.
#
# VERIFIERAT genom att anropa riktiga _health()/_btc_lage() timme för timme mot
# 37 innehav / 191 innehavsdygn (verifiera_btc_topp.py): larmen går från 0.87
# till 0.90 per coin och dygn. Första 📉 kom tidigare i 5 innehav — bättre pris i
# 4 (ETHFI +6.9% i stället för +2.0%, WLD +5.5/+3.9, AVAX +3.5/+2.4, UNI
# +3.1/+2.4) och sämre i 1 som kostade mer än de fyra gav: ZEC +3.6% i stället
# för +19.3%. Samma mönster som alla vinstskydd: små räddningar, dyra missar.
# Skeppat för att användaren uttryckligen vill ha varningen tidigare — det är
# information, inte en säljorder, och avvägningen står i utskicket.
BTC_CONFIRM_COIN = 0.03     # coinet minst 3% från din topp ...
BTC_CONFIRM_BTC = 0.015     # ... OCH BTC minst 1.5% från SIN topp sedan köpet


def _btc_lage(conn, ts):
    """(BTC:s rörelse sedan `ts`, BTC:s avstånd från sin topp sedan `ts`).

    Rörelsen skiljer coinets egen svaghet från marknadens; avståndet från toppen
    är det som bekräftar att marknaden vänder. Båda på STÄNGDA barer — den
    pågående baren har partiell data (samma regel som allt annat som läser pris).
    """
    cid = db.load_coin_ids(conn).get("BTC")
    if not cid:
        return None, None
    df = db.load_ohlcv_df(conn, cid, "1h")
    if df.empty or _is_stale(df, "1h"):
        return None, None
    pos = len(df) + _last_closed_idx(df, "1h")
    i0 = df.index.get_indexer([ts], method="nearest")[0]
    if abs((df.index[i0] - ts).total_seconds()) > 6 * 3600 or i0 > pos:
        return None, None
    c = df["close"]
    nu = float(c.iloc[pos])
    return nu / float(c.iloc[i0]) - 1, nu / float(c.iloc[i0:pos + 1].max()) - 1


def _health(conn, h: dict, df, i: int, close: float, pl_frac: float, pl: str,
            send: bool = True):
    """🔎 Håller grunden för köpet? Returnerar larmtext eller None.

    Kollas varje timme men skickas som mest var HEALTH_DEDUP_HOURS:e timme, så
    du får en uppdatering några gånger per dygn i stället för en engångskoll.
    """
    timmar = (datetime.now(timezone.utc) - h["opened_at"]).total_seconds() / 3600
    if timmar < HEALTH_MIN_HOURS:
        return None
    if (h["coin_id"], "health") in db.recent_radar_alerts(conn, HEALTH_DEDUP_HOURS):
        return None

    rader, traffar = [], 0

    oi = db.oi_since(conn, h["coin_id"], h["opened_at"])
    if oi is None:
        rader.append("  ➖ OI saknas för det här coinet")
    elif oi <= HEALTH_OI_LEAK:
        traffar += 1
        rader.append(f"  ⚠️ <b>Pengarna lämnade:</b> OI {oi*100:+.1f}% sedan du köpte")
    else:
        rader.append(f"  ✅ Pengarna kvar: OI {oi*100:+.1f}% sedan köp")

    # i kommer från _last_closed_idx() och är NEGATIVT (-1 eller -2). Koden hade
    # max(0, i-11) vilket kollapsar till 0 för negativa i, så slicen blev
    # iloc[0:-1] = HELA historiken (2827 barer) i stället för de senaste tolv.
    # Kvoten blev 4.85 där den skulle vara 0.85, och villkoret "intresset dog"
    # kunde därmed ALDRIG bli sant. Normalisera till positivt index först.
    pos = len(df) + i if i < 0 else i
    volbas = df["volume"].rolling(48).mean().iloc[pos]
    volnu = float(df["volume"].iloc[max(0, pos - 11):pos + 1].mean())
    kvot = volnu / float(volbas) if volbas else None
    if kvot is None:
        rader.append("  ➖ Volymen går inte att jämföra")
    elif kvot < HEALTH_VOL_DEAD:
        traffar += 1
        rader.append(f"  ⚠️ <b>Intresset dog:</b> volymen {kvot:.1f}× av det normala")
    else:
        rader.append(f"  ✅ Volym kvar: {kvot:.1f}× av det normala")

    btc, btc_topp = _btc_lage(conn, h["opened_at"])
    if btc is None:
        rader.append("  ➖ Kan inte jämföra mot BTC")
    else:
        rel = pl_frac - btc
        if rel <= HEALTH_LAG_BTC:
            traffar += 1
            rader.append(f"  ⚠️ <b>Släpar efter marknaden:</b> {rel*100:+.1f}% mot BTC sedan köp")
        else:
            rader.append(f"  ✅ Håller jämna steg: {rel*100:+.1f}% mot BTC sedan köp")

    # 📉 Vinsten rinner tillbaka — räcker ENSAMT. Se konstant-blocket: det är
    # det här läget som ETHFI gick igenom helt tyst.
    vol = features.daily_vol(db.load_recent_closes(conn, h["coin_id"], "1h", 240))
    band = max(PEAK_DROP_MIN, PEAK_DROP_VOL_MULT * vol) if vol else PEAK_DROP_MIN
    hw = max(h["high_water"], h["entry"])
    topp_pl = hw / h["entry"] - 1
    fran_topp = close / hw - 1
    tappat = topp_pl - pl_frac
    armad = hw >= h["entry"] * PEAK_ARM
    peak_larm = armad and fran_topp <= -band
    # Marknaden vänder också — se BTC_CONFIRM-blocket. Samma armering som ovan,
    # så det aldrig larmar på en position som inte varit ordentligt i vinst.
    btc_larm = (armad and not peak_larm and btc_topp is not None
                and fran_topp <= -BTC_CONFIRM_COIN and btc_topp <= -BTC_CONFIRM_BTC)

    if peak_larm:
        rader.insert(0, f"  📉 <b>Vinsten rinner tillbaka:</b> toppade {topp_pl*100:+.1f}%, "
                        f"du har tappat {tappat*100:.1f} procentenheter därifrån")
    elif btc_larm:
        rader.insert(0, f"  📉 <b>Marknaden vänder också:</b> toppade {topp_pl*100:+.1f}%, "
                        f"du har tappat {tappat*100:.1f} procentenheter därifrån — och "
                        f"BTC ligger {btc_topp*100:.1f}% under sin topp sedan du köpte")

    if not (peak_larm or btc_larm) and traffar < HEALTH_MIN_HITS:
        return None

    kr = ""
    if h.get("amount"):
        kr = f" ({h['amount'] * pl_frac:+,.0f} kr)".replace(",", " ")
    # Loggas bara skarpt: loggen är också dedupen, så en testkörning med
    # --no-send hade annars tystat det riktiga larmet i HEALTH_DEDUP_HOURS.
    if send:
        db.record_radar_alerts(conn, [(h["coin_id"], "health", {
            "pl": round(pl_frac, 4), "oi_since": None if oi is None else round(oi, 4),
            "vol_kvot": None if kvot is None else round(kvot, 2),
            "vs_btc": None if btc is None else round(pl_frac - btc, 4),
            "topp_pl": round(topp_pl, 4), "fran_topp": round(fran_topp, 4),
            "btc_topp": None if btc_topp is None else round(btc_topp, 4),
            "peak_larm": peak_larm, "btc_larm": btc_larm, "traffar": traffar,
            "timmar": round(timmar), "price": close,
        })])
    rubrik = ("vinsten rinner tillbaka" if peak_larm
              else "toppen kan vara satt" if btc_larm
              else "grunden för köpet håller inte längre")
    stop_txt = (f" · stoppen {(close/h['stop']-1)*100:+.0f}% bort"
                if h["stop"] else "")
    return (
        f"🔎 <b>{h['symbol']}: {rubrik}</b>\n"
        f"  Du ligger {pl}{kr} · håller sedan {timmar/24:.0f} d{stop_txt}\n"
        + "\n".join(rader) + "\n"
        f"  <i>Inget säljråd. Mätt 23 sep på dina 28 senaste avslutade trades: "
        f"att sälja på första 🔎 hade gett bättre pris än ditt eget sälj 18 "
        f"gånger — men de 10 gångerna det blev sämre var det dina största "
        f"vinnare (ZEC +60%, POL +35%, OP +20%, WLD +21%), och totalt hade du "
        f"förlorat på det. Det räddar små belopp och kan kosta stora. Beslutet "
        f"är ditt.</i>"
    )


def _check_holding(conn, h: dict, timeframe: str, send: bool = True) -> list:
    """Returnerar larmrader för ett innehav (och uppdaterar high_water/flaggor).

    Med send=False skrivs ingen LARMSTATUS (dedup-logg, stop_alerted,
    trail_alert_at) — den styr vad som får gå ut nästa timme, och en torrkörning
    får aldrig tysta ett riktigt larm. high_water uppdateras ändå: det är en
    observation av priset, inte ett larm.
    """
    msgs = []
    df = db.load_ohlcv_df(conn, h["coin_id"], timeframe)
    if len(df) < 60 or _is_stale(df, timeframe):
        return msgs
    i = _last_closed_idx(df, timeframe)
    close = float(df["close"].iloc[i])
    entry, hw = h["entry"], h["high_water"]
    pl_frac = close / entry - 1
    pl = f"{pl_frac*100:+.1f}%"

    # Ny topp? (uppdatera high water mark)
    if close > hw:
        hw = close
        db.update_holding(conn, h["id"], high_water=close)

    # ❌ STOP — upprepas ~1×/dygn så länge positionen ligger under stoppen.
    if h["stop"] is not None and close <= h["stop"]:
        if (h["coin_id"], "stop") not in db.recent_radar_alerts(conn, STOP_REMIND_HOURS):
            days = (datetime.now(timezone.utc) - h["opened_at"]).days
            again = " (påminnelse)" if h["stop_alerted"] else ""
            msgs.append(
                f"❌ <b>{h['symbol']}: under stoppen{again}</b>\n"
                f"  nu {close:g} ≤ stop {h['stop']:g} · sedan köp: {pl} · håller sedan {days} d\n"
                f"  <i>Överväg att sälja — stoppen fanns där av en anledning.</i>"
            )
            if send:
                db.record_radar_alerts(conn, [(h["coin_id"], "stop")])
                if not h["stop_alerted"]:
                    db.update_holding(conn, h["id"], stop_alerted=True)

    # 📉 TRAIL — bara när du FAKTISKT är i vinst och rörelsen viker från toppen.
    vol = features.daily_vol(db.load_recent_closes(conn, h["coin_id"], timeframe, 240))
    trail_pct = max(TRAIL_MIN, TRAIL_VOL_MULT * vol) if vol else TRAIL_MIN
    armed = hw >= entry * PROFIT_ARM                     # toppen har varit rejält uppe
    in_profit = close >= entry * (1 + MIN_PROFIT_NOW)    # och det finns vinst kvar NU
    rearmed = h["trail_alert_at"] is None or hw > h["trail_alert_at"]
    if armed and in_profit and rearmed and close <= hw * (1 - trail_pct):
        msgs.append(
            f"📉 <b>{h['symbol']}: rörelsen viker</b>\n"
            f"  topp {hw:g} → nu {close:g} ({(close/hw-1)*100:+.1f}% från toppen) · sedan köp: {pl}\n"
            f"  <i>Överväg att säkra vinst — toppen kan vara satt.</i>"
        )
        if send:
            db.update_holding(conn, h["id"], trail_alert_at=hw)

    snap = scout._snapshot(conn, SimpleNamespace(symbol=h["symbol"]), h["coin_id"], timeframe)

    # 🔎 HÄLSOKOLL — finns grunden för köpet kvar? Se konstant-blocket ovan.
    if not msgs:
        hm = _health(conn, h, df, i, close, pl_frac, pl, send)
        if hm:
            msgs.append(hm)

    # 🔴 SÄLJVOLYM i ett coin du äger (scout-mönstret, med egen dedup)
    if snap and not msgs and scout.classify(snap) == "distribution":
        recent = db.recent_radar_alerts(conn, DIST_DEDUP_HOURS)
        if (h["coin_id"], "exit_dist") not in recent:
            # Slutklämmen måste matcha ditt FAKTISKA läge — larmet triggar på att
            # COINET stigit, inte på att du ligger plus. Att säga "säkra vinst" till
            # någon som ligger back är samma fel som trail-larmet hade.
            if pl_frac >= MIN_PROFIT_NOW:
                advice = "Säljare kliver in — överväg att säkra vinst."
            elif pl_frac >= 0:
                advice = "Säljare kliver in medan du är nära nollan — bevaka noga."
            else:
                stop_txt = f" Din stop ligger på {h['stop']:g}." if h["stop"] else ""
                advice = ("Säljare kliver in medan du ligger back — rörelsen kan "
                          f"fortsätta ner.{stop_txt}")
            msgs.append(
                f"🔴 <b>{h['symbol']}: säljvolym</b>\n"
                f"  {snap['vol_ratio']:.1f}× volym, 6h {snap['mom_short']*100:+.0f}% · sedan köp: {pl}\n"
                f"  <i>{advice}</i>"
            )
            if send:
                db.record_radar_alerts(conn, [(h["coin_id"], "exit_dist")])
    return msgs


def run(conn, timeframe: str = "1h", send: bool = True) -> int:
    db.ensure_exit_tables(conn)
    holdings = db.load_open_holdings(conn)
    if not holdings:
        print("Inga bevakade innehav.")
        return 0

    all_msgs = []
    for h in holdings:
        all_msgs.extend(_check_holding(conn, h, timeframe, send))

    if all_msgs:
        text = "👜 <b>DINA INNEHAV</b>\n\n" + "\n\n".join(all_msgs)
        print(text)
        if send:
            alerts.send(text)
            print("\n[skickat till Telegram]")
    else:
        print(f"{len(holdings)} innehav bevakade — inget att larma om.")
    return len(all_msgs)

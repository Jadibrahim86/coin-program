"""Exit-vakt: kollar dina bevakade innehav (holdings) varje timme och larmar när
det är läge att sälja. Reaktiv, inte förutsägande — vi försöker INTE pricka toppen,
vi reagerar när rörelsen viker.

Fyra larm per innehav:
  ❌ STOP   — priset bröt din stop (upprepas ~1×/dygn så länge det ligger under)
  🟠 VINSTEN VÄNDER — du ligger några procent plus och säljvolym kliver in
              (det TIDIGA larmet — se nedan)
  📉 TRAIL  — du är FAKTISKT i vinst och priset har vikt ner från toppen
              (bandet skalas mot coinets dagsvolatilitet; åter-aktiveras vid ny topp)
  🔴 SÄLJVOLYM — ovanligt hög volym + vikande momentum i ett coin du äger

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

# --- Tidigt vinstlarm (🟠) ----------------------------------------------------
# Trösklarna här är INTE mätta mot utfall — de är satta ur användarens uttalade
# preferens ("jag tar gärna 3-5% på plussidan") och ur hur de tre senaste
# förlusterna såg ut (CHZ/ATOM/TAO toppade mellan +2% och +5%). De ska mätas mot
# radar_alerts-loggen om några veckor: larmade den för tidigt (priset gick vidare
# upp) eller i tid (priset fortsatte ner)?
EARLY_MIN_PROFIT = 0.02     # måste ligga minst +2% för att larmet ens övervägs
EARLY_VOL = 4.0             # × snittvolym. Lägre än radarns 6× med flit: vi tittar
                            # på 4 coins vi äger, inte letar nål i 34 — men högt nog
                            # att det ska vara verklig säljvolym, inte drift.
EARLY_TURN_MIN = 0.015      # momentum-vändning: max(1.5%, 0.5 × dagsvol) ner på 6h.
EARLY_TURN_VOL_MULT = 0.5   # volskalat så ett 8%/dag-coin inte larmar på brus.
EARLY_DEDUP_HOURS = 8


def _check_holding(conn, h: dict, timeframe: str) -> list:
    """Returnerar larmrader för ett innehav (och uppdaterar high_water/flaggor)."""
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
        db.update_holding(conn, h["id"], trail_alert_at=hw)

    snap = scout._snapshot(conn, SimpleNamespace(symbol=h["symbol"]), h["coin_id"], timeframe)

    # 🟠 TIDIGT VINSTLARM — du ligger plus och säljvolym kliver in. Se docstringen:
    # det här är larmet som saknades när CHZ/ATOM/TAO toppade under +6% och gick
    # hela vägen till stoppen utan ett ord. Hoppas över om stop/trail redan larmat.
    if snap and not msgs and pl_frac >= EARLY_MIN_PROFIT:
        turn = -max(EARLY_TURN_MIN, EARLY_TURN_VOL_MULT * vol) if vol else -EARLY_TURN_MIN
        if snap["vol_ratio"] >= EARLY_VOL and snap["mom_short"] <= turn:
            if (h["coin_id"], "early_profit") not in db.recent_radar_alerts(conn, EARLY_DEDUP_HOURS):
                kr = ""
                if h.get("amount"):
                    gain = h["amount"] * pl_frac
                    kr = f" ({gain:+,.0f} kr)".replace(",", " ")
                _, oitxt = scout.oi_label("distribution", snap["oi_chg"])
                msgs.append(
                    f"🟠 <b>{h['symbol']}: vinsten vänder</b>\n"
                    f"  du ligger {pl}{kr} · säljvolym {snap['vol_ratio']:.1f}× snittet, "
                    f"6h {snap['mom_short']*100:+.1f}%\n"
                    f"  {oitxt}\n"
                    f"  <i>Överväg att ta vinsten här. Det kan mycket väl gå vidare upp — "
                    f"men de senaste förlusterna såg ut precis så här strax innan de vände "
                    f"ner genom ingången och vidare till stoppen.</i>"
                )
                db.record_radar_alerts(conn, [(h["coin_id"], "early_profit", {
                    "pl": round(pl_frac, 4), "vol_ratio": round(snap["vol_ratio"], 1),
                    "mom_short": round(snap["mom_short"], 4), "oi_chg": snap["oi_chg"],
                    "price": close,
                })])

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
        all_msgs.extend(_check_holding(conn, h, timeframe))

    if all_msgs:
        text = "👜 <b>DINA INNEHAV</b>\n\n" + "\n\n".join(all_msgs)
        print(text)
        if send:
            alerts.send(text)
            print("\n[skickat till Telegram]")
    else:
        print(f"{len(holdings)} innehav bevakade — inget att larma om.")
    return len(all_msgs)

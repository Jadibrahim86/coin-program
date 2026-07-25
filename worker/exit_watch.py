"""Exit-vakt: kollar dina bevakade innehav (holdings) varje timme och larmar när
det är läge att sälja. Reaktiv, inte förutsägande — vi försöker INTE pricka toppen,
vi reagerar när rörelsen viker.

Tre larm per innehav:
  ❌ STOP   — priset bröt din stop (upprepas ~1×/dygn så länge det ligger under)
  📉 TRAIL  — du är FAKTISKT i vinst och priset har vikt ner från toppen
              (bandet skalas mot coinets dagsvolatilitet; åter-aktiveras vid ny topp)
  🔴 SÄLJVOLYM — ovanligt hög volym + vikande momentum i ett coin du äger

Kalibrering 2026-07-25 efter en vecka med verklig data: trailen larmade tidigare vid
+2% över entry med 3%-band, vilket kapade vinnare vid ~0% (RAY larmade t.o.m. "säkra
vinst" på -1.1% förlust) medan förluster fick löpa till full stop. Trösklarna nedan
gör larmen symmetriska: vinnare får utrymme, och "säkra vinst" sägs bara i vinst.
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


def _check_holding(conn, h: dict, timeframe: str) -> list:
    """Returnerar larmrader för ett innehav (och uppdaterar high_water/flaggor)."""
    msgs = []
    df = db.load_ohlcv_df(conn, h["coin_id"], timeframe)
    if len(df) < 60 or _is_stale(df, timeframe):
        return msgs
    i = _last_closed_idx(df, timeframe)
    close = float(df["close"].iloc[i])
    entry, hw = h["entry"], h["high_water"]
    pl = f"{(close/entry-1)*100:+.1f}%"

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

    # 🔴 SÄLJVOLYM i ett coin du äger (scout-mönstret, med egen dedup)
    snap = scout._snapshot(conn, SimpleNamespace(symbol=h["symbol"]), h["coin_id"], timeframe)
    if snap and scout.classify(snap) == "distribution":
        recent = db.recent_radar_alerts(conn, DIST_DEDUP_HOURS)
        if (h["coin_id"], "exit_dist") not in recent:
            msgs.append(
                f"🔴 <b>{h['symbol']}: säljvolym</b>\n"
                f"  {snap['vol_ratio']:.1f}× volym, 6h {snap['mom_short']*100:+.0f}% · sedan köp: {pl}\n"
                f"  <i>Säljare kliver in — överväg att säkra vinst.</i>"
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

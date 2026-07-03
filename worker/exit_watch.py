"""Exit-vakt: kollar dina bevakade innehav (holdings) varje timme och larmar när
det är läge att sälja. Reaktiv, inte förutsägande — vi försöker INTE pricka toppen,
vi reagerar när rörelsen viker.

Tre larm per innehav:
  ❌ STOP   — priset bröt din stop (skickas en gång)
  📉 TRAIL  — du är i vinst men priset har vikt ner från toppen (ATR-anpassat;
              åter-aktiveras om coinet sätter en ny topp efter larmet)
  🔴 SÄLJVOLYM — ovanligt hög volym + vikande momentum i ett coin du äger
"""
from types import SimpleNamespace

import numpy as np

import alerts
import db
import features
import scout
from live_signals import _is_stale, _last_closed_idx

TRAIL_MIN = 0.03      # trail-tröskel = max(3%, 1.5 × ATR%)
TRAIL_ATR_MULT = 1.5
PROFIT_ARM = 1.02     # trail aktiveras först när toppen är ≥ +2% över entry
DIST_DEDUP_HOURS = 8  # säljvolym-larm per coin max var 8:e timme


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

    # ❌ STOP
    if h["stop"] is not None and close <= h["stop"] and not h["stop_alerted"]:
        msgs.append(
            f"❌ <b>{h['symbol']}: stoppen bruten</b>\n"
            f"  nu {close:g} ≤ stop {h['stop']:g} · sedan köp: {pl}\n"
            f"  <i>Överväg att sälja — stoppen fanns där av en anledning.</i>"
        )
        db.update_holding(conn, h["id"], stop_alerted=True)

    # 📉 TRAIL — i vinst, men priset viker från toppen
    atr = features.compute(df)["atr"].iloc[i]
    trail_pct = max(TRAIL_MIN, TRAIL_ATR_MULT * float(atr) / close) if not np.isnan(atr) else TRAIL_MIN
    armed = hw >= entry * PROFIT_ARM
    rearmed = h["trail_alert_at"] is None or hw > h["trail_alert_at"]
    if armed and rearmed and close <= hw * (1 - trail_pct):
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
                f"  {snap['vol_ratio']:.1f}× volym, 12h {snap['mom_short']*100:+.0f}% · sedan köp: {pl}\n"
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

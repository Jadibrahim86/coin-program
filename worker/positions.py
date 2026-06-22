"""Köp→sälj-livscykel (LONG ONLY).

- KÖP-alert när ett nytt long-setup dyker upp (på en coin du inte redan "äger").
- SÄLJ-alert när en öppen position träffar sin stop eller sitt mål (TP).

Boten MINNS öppna positioner via signals (öppna) + signal_outcomes (stängda). Detta är
den schemalagda "pulsen" som molnet kör några gånger per dygn.

⚠️ EJ VALIDERAD — och long-only är inte om-validerad än (validate.py testade long+short).
"""
import numpy as np

import alerts
import config
import db
import features
from live_signals import (
    MIN_BARS, RR_TARGET, K_ATR_STOP,
    _confidence, _is_stale, _last_closed_idx, _regime,
)

BUY_MIN_CONF = 50.0  # alerta bara hyfsade setups → färre, bättre trades (cost-medvetet)


def _long_candidate(conn, coin, cid: int, timeframe: str):
    """Long-setup på senast stängda baren, eller None. Bara LONG."""
    df = db.load_ohlcv_df(conn, cid, timeframe)
    if len(df) < MIN_BARS or _is_stale(df, timeframe):
        return None  # för lite data, ELLER inaktuell (t.ex. coin som molnbörsen saknar)
    f = features.compute(df)
    idx = _last_closed_idx(df, timeframe)
    last = f.iloc[idx]
    if not (last["trend"] == 1 and last["rsi"] > 50 and last["vol_z"] > 0):
        return None
    atr = last["atr"]
    if atr is None or np.isnan(atr) or atr <= 0:
        return None

    entry = float(last["close"])
    stop_dist = K_ATR_STOP * atr
    stop = float(entry - stop_dist)
    tp = float(entry + RR_TARGET * stop_dist)

    daily = db.load_ohlcv_df(conn, cid, "1d")
    daily_trend = 0
    if len(daily) >= MIN_BARS:
        daily_trend = int(features.compute(daily).iloc[_last_closed_idx(daily, "1d")]["trend"])
    conf = _confidence(last, 1, daily_trend)
    if conf < BUY_MIN_CONF:
        return None

    atr_pctile = None if np.isnan(last["atr_pctile"]) else round(float(last["atr_pctile"]), 2)
    triggers = {
        "trend": int(last["trend"]), "rsi": round(float(last["rsi"]), 1),
        "vol_z": round(float(last["vol_z"]), 2), "daily_trend": daily_trend,
        "mtf_agree": daily_trend == 1, "atr_pctile": atr_pctile,
    }
    return {
        "row": (cid, df.index[idx].to_pydatetime(), "long", conf, entry, stop,
                [round(tp, 6)], RR_TARGET, conf, triggers, _regime(last["atr_pctile"])),
        "symbol": coin.symbol, "conf": conf, "entry": entry, "stop": stop, "tp": tp,
        "mtf": daily_trend == 1,
    }


def _check_exit(conn, rec, timeframe):
    """Träffade en öppen rek stop eller TP efter entry? (outcome, exit_price, ts) | None."""
    for ts, high, low in db.load_bars_since(conn, rec["coin_id"], timeframe, rec["ts"]):
        if low <= rec["stop"]:
            return "stop", rec["stop"], ts
        if high >= rec["tp"]:
            return "tp", rec["tp"], ts
    return None


def _buy_message(buys) -> str:
    lines = ["🟢 <b>KÖP-signal</b> (EJ VALIDERAD)\n"]
    for b in buys:
        mtf = " ✓daily" if b["mtf"] else ""
        lines.append(
            f"<b>{b['symbol']}</b>  conf {b['conf']:.0f}{mtf}\n"
            f"  köp ~{b['entry']:g} · stop {b['stop']:g} · mål {b['tp']:g} · RR {RR_TARGET:g}"
        )
    lines.append("\n<i>Agera inte på dessa än — strategin är inte validerad.</i>")
    return "\n".join(lines)


def _sell_message(sells) -> str:
    lines = ["🔴 <b>SÄLJ-signal</b> (EJ VALIDERAD)\n"]
    for rec, outcome, price in sells:
        label = "✅ nådde målet" if outcome == "tp" else "❌ träffade stop"
        lines.append(f"<b>{rec['symbol']}</b>  {label}\n  sälj nu (~{price:g})")
    lines.append("\n<i>Spårning av tidigare köp-signaler.</i>")
    return "\n".join(lines)


def run(conn, timeframe: str = "4h", send: bool = True) -> dict:
    coin_ids = db.load_coin_ids(conn)

    # 1) Kolla öppna positioner för exit (sälj).
    sells, still_open = [], set()
    for rec in db.load_open_recommendations(conn):
        ex = _check_exit(conn, rec, timeframe)
        if ex:
            outcome, exit_price, closed_ts = ex
            db.insert_outcome(conn, rec["id"], outcome, RR_TARGET if outcome == "tp" else -1.0, closed_ts)
            sells.append((rec, outcome, exit_price))
        else:
            still_open.add(rec["coin_id"])

    # 2) Nya köp för coins vi inte redan "äger".
    buys = []
    for coin in config.UNIVERSE:
        cid = coin_ids.get(coin.symbol)
        if cid is None or cid in still_open:
            continue
        cand = _long_candidate(conn, coin, cid, timeframe)
        if cand:
            buys.append(cand)
    if buys:
        db.insert_signals(conn, [b["row"] for b in buys])

    # 3) Alerts.
    buys.sort(key=lambda b: b["conf"], reverse=True)
    if send:
        if sells:
            alerts.send(_sell_message(sells))
        if buys:
            alerts.send(_buy_message(buys))

    print(f"[{timeframe}] SÄLJ: {len(sells)}  KÖP: {len(buys)}  (öppna kvar: {len(still_open)})")
    for rec, outcome, price in sells:
        print(f"  SÄLJ {rec['symbol']:<5} {outcome} @ {price:g}")
    for b in buys:
        print(f"  KÖP  {b['symbol']:<5} conf {b['conf']:.0f}  entry {b['entry']:g} stop {b['stop']:g} mål {b['tp']:g}")
    return {"sells": len(sells), "buys": len(buys), "open": len(still_open)}

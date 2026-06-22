"""Live signalgenerering (grund för Fas 5-pipen).

Beräknar AKTUELLA setups på senaste baren och skriver till signals-tabellen. Använder
EXAKT samma feature-/signaldefinition som backtesten (features.py / signals.py) — det du
ser live är samma logik som testats.

⚠️ EJ VALIDERAD: strategin har inte klarat walk-forward/out-of-sample än. Signalerna här
är PRELIMINÄRA — för att se/känna produkten, inte för att agera på med riktiga pengar.
"""
from datetime import datetime, timezone

import numpy as np

import config
import db
import features

K_ATR_STOP = 1.5
RR_TARGET = 2.0
MIN_BARS = 250


def _last_closed_idx(df, timeframe: str) -> int:
    """Index för senast STÄNGDA baren. CCXT inkluderar ofta den pågående baren sist;
    den har partiell OHLCV och får aldrig styra en live-signal."""
    secs = config.TIMEFRAME_SECONDS[timeframe]
    last_open = df.index[-1].to_pydatetime().timestamp()
    if last_open + secs > datetime.now(timezone.utc).timestamp():
        return -2  # sista baren formas fortfarande → använd näst sista
    return -1


def _confidence(row, direction: int, daily_trend: int) -> float:
    """0–100: blend av RSI-styrka, volymbekräftelse och daily-trend-överens (MTF)."""
    rsi = row["rsi"]
    rsi_comp = np.clip((rsi - 50) / 50 if direction == 1 else (50 - rsi) / 50, 0, 1)
    vol_comp = float(np.clip(row["vol_z"] / 2.0, 0, 1))
    mtf_comp = 1.0 if daily_trend == direction else 0.0
    return float(round(100 * np.mean([rsi_comp, vol_comp, mtf_comp]), 1))


def _regime(atr_pctile) -> str | None:
    if atr_pctile is None or np.isnan(atr_pctile):
        return None
    if atr_pctile > 0.8:
        return "high_vol"
    if atr_pctile < 0.2:
        return "low_vol"
    return "normal"


def generate(conn, timeframe: str = "4h", verbose: bool = True) -> list:
    coin_ids = db.load_coin_ids(conn)
    id2sym = {v: k for k, v in coin_ids.items()}
    rows = []

    for coin in config.UNIVERSE:
        cid = coin_ids.get(coin.symbol)
        if cid is None:
            continue
        df = db.load_ohlcv_df(conn, cid, timeframe)
        if len(df) < MIN_BARS:
            continue
        f = features.compute(df)
        idx = _last_closed_idx(df, timeframe)
        last = f.iloc[idx]
        bar_ts = df.index[idx].to_pydatetime()

        long_ok = last["trend"] == 1 and last["rsi"] > 50 and last["vol_z"] > 0
        short_ok = last["trend"] == -1 and last["rsi"] < 50 and last["vol_z"] > 0
        if not (long_ok or short_ok):
            continue
        direction = 1 if long_ok else -1

        atr = last["atr"]
        if atr is None or np.isnan(atr) or atr <= 0:
            continue
        entry = float(last["close"])
        stop_dist = K_ATR_STOP * atr
        stop = float(entry - direction * stop_dist)
        tp = float(entry + direction * RR_TARGET * stop_dist)

        # Multi-timeframe: stämmer daily-trenden? (§3.3 — daily som riktningsfilter.)
        daily = db.load_ohlcv_df(conn, cid, "1d")
        daily_trend = 0
        if len(daily) >= MIN_BARS:
            didx = _last_closed_idx(daily, "1d")
            daily_trend = int(features.compute(daily).iloc[didx]["trend"])

        conf = _confidence(last, direction, daily_trend)
        atr_pctile = None if np.isnan(last["atr_pctile"]) else round(float(last["atr_pctile"]), 2)
        triggers = {
            "trend": int(last["trend"]),
            "rsi": round(float(last["rsi"]), 1),
            "vol_z": round(float(last["vol_z"]), 2),
            "daily_trend": daily_trend,
            "mtf_agree": daily_trend == direction,
            "atr_pctile": atr_pctile,
        }
        rows.append((
            cid, bar_ts,
            "long" if direction == 1 else "short",
            conf, entry, stop, [round(tp, 6)], RR_TARGET, conf, triggers,
            _regime(last["atr_pctile"]),
        ))

    n = db.insert_signals(conn, rows)
    rows.sort(key=lambda r: r[3], reverse=True)  # rangordna på confidence

    if verbose:
        print(f"\n[EJ VALIDERAD] preliminära signaler ({timeframe}), {n} aktiva setups:\n")
        if not rows:
            print("  (inga aktiva setups just nu)")
        for r in rows:
            cid, ts, direction, score = r[0], r[1], r[2], r[3]
            entry, stop, tp = r[4], r[5], r[6][0]
            agree = "MTF" if r[9]["mtf_agree"] else "-"
            print(f"  {id2sym.get(cid,'?'):<5} {direction:<5} conf {score:>5.1f}  "
                  f"entry {entry:<12.4f} stop {stop:<12.4f} tp {tp:<12.4f} {agree}")
    return rows

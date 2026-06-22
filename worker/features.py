"""Feature-beräkning (Fas 2). Rena funktioner: OHLCV-DataFrame in → features ut.

KAUSALT per konstruktion: värdet på rad t använder bara rader <= t (rolling/ewm
tittar bakåt). Samma kod används live (skriv till features-tabellen) och i backtest
(beräkna på historiska slices) — så definitionerna kan aldrig glida isär.

Vi håller oss till FÅ, MEDVETET OKORRELERADE familjer (§3 i PLAN.md):
  trend  – riktning (EMA-struktur)
  momentum – RSI
  volatilitet – ATR + ATR-percentil (regim + positionsstorlek)
  volym – volym-z-score (bekräftelse)
"""
import numpy as np
import pandas as pd

# Standardperioder. Håll få och robusta — inte överoptimerade.
EMA_FAST, EMA_SLOW, EMA_TREND = 20, 50, 200
RSI_PERIOD = 14
ATR_PERIOD = 14
ATR_PCTILE_WINDOW = 100
VOL_WINDOW = 20


def _ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()


def _rsi(close: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    # avg_loss == 0 ger rs = inf → RSI = 100 (stark uptrend), vilket är korrekt.
    # avg_gain == avg_loss == 0 ger 0/0 = NaN → neutral 50.
    rs = avg_gain / avg_loss
    return (100 - 100 / (1 + rs)).fillna(50)


def _atr(df: pd.DataFrame, period: int = ATR_PERIOD) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def compute(df: pd.DataFrame) -> pd.DataFrame:
    """df indexerad på ts med kolumner open/high/low/close/volume (stigande tid).

    Returnerar samma index med OHLCV + feature-kolumner.
    """
    out = df.copy()

    ema_fast = _ema(out["close"], EMA_FAST)
    ema_slow = _ema(out["close"], EMA_SLOW)
    ema_trend = _ema(out["close"], EMA_TREND)

    # Trend: +1 uptrend, -1 downtrend, 0 oklart. Pris vs långsam EMA + EMA-ordning.
    up = (out["close"] > ema_trend) & (ema_fast > ema_slow)
    down = (out["close"] < ema_trend) & (ema_fast < ema_slow)
    out["trend"] = np.where(up, 1, np.where(down, -1, 0))

    # Momentum.
    out["rsi"] = _rsi(out["close"])

    # Volatilitet: ATR + percentilrang i rullande fönster (regim 0..1).
    out["atr"] = _atr(out)
    out["atr_pctile"] = (
        out["atr"]
        .rolling(ATR_PCTILE_WINDOW)
        .apply(lambda a: (a <= a[-1]).mean(), raw=True)
    )

    # Volymbekräftelse: z-score mot rullande medel.
    vmean = out["volume"].rolling(VOL_WINDOW).mean()
    vstd = out["volume"].rolling(VOL_WINDOW).std()
    out["vol_z"] = (out["volume"] - vmean) / vstd.replace(0, np.nan)

    return out


# Vilka feature-kolumner som sparas till features-tabellen (live-vägen).
FEATURE_COLUMNS = ["trend", "rsi", "atr", "atr_pctile", "vol_z"]

"""Volym-spik-före-pris-signal (LONG ONLY, kort timeframe).

Tes (din idé): stor volym kommer IN i en coin men priset har ÄNNU INTE reagerat —
hög volym men liten prisrörelse = absorption/ackumulation → priset väntas röra sig upp.

Pure & kausal: OHLCV-df in → df med open/high/low/close/signal/atr. Rolling tittar bakåt.
"""
import numpy as np
import pandas as pd

import features as feat

VOL_BASELINE = 96      # bars för volym-snitt (24h på 15m)
VOL_SPIKE = 3.0        # volym >= X * snitt = spik
MAX_BODY_ATR = 0.4     # |close-open| < detta * ATR  → "priset har inte reagerat (än)"
ATR_PERIOD = 14


def generate(ohlcv: pd.DataFrame) -> pd.DataFrame:
    df = ohlcv.copy()
    atr = feat._atr(df, ATR_PERIOD)
    vol_sma = df["volume"].rolling(VOL_BASELINE).mean()
    vol_ratio = df["volume"] / vol_sma.replace(0, np.nan)
    body = (df["close"] - df["open"]).abs()

    # Volym-spik + liten prisrörelse + svagt upp (ackumulation, ej distribution).
    long_ok = (
        (vol_ratio >= VOL_SPIKE)
        & (body < MAX_BODY_ATR * atr)
        & (df["close"] >= df["open"])
    )
    df["signal"] = np.where(long_ok.fillna(False), 1, 0)
    df["atr"] = atr
    return df[["open", "high", "low", "close", "signal", "atr"]].copy()

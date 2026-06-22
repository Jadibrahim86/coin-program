"""Minimal regelbaserad signal (Fas 3).

Avsiktligt enkel — poängen är att nå GRINDEN (en trovärdig backtest mot baseline),
inte att vara en bra strategi än. Signalen genereras vid varje bars CLOSE och
exekveras sen på nästa bars open i motorn (backtest_engine.py) → ingen lookahead.

Regel (long): uptrend + RSI > 50 + volym bekräftar (vol_z > 0).
Regel (short): downtrend + RSI < 50 + volym bekräftar.
ATR vid signalbaren bär stop/positionsstorlek vidare till motorn.
"""
import numpy as np
import pandas as pd

import features

RSI_LONG = 50
RSI_SHORT = 50
VOL_CONFIRM = 0.0  # vol_z-tröskel


def generate(ohlcv: pd.DataFrame) -> pd.DataFrame:
    """OHLCV in → DataFrame med kolumner motorn behöver: open/high/low/close/signal/atr.

    signal: +1 long, -1 short, 0 flat (beslut per bars close).
    """
    f = features.compute(ohlcv)

    long_ok = (f["trend"] == 1) & (f["rsi"] > RSI_LONG) & (f["vol_z"] > VOL_CONFIRM)
    short_ok = (f["trend"] == -1) & (f["rsi"] < RSI_SHORT) & (f["vol_z"] > VOL_CONFIRM)
    f["signal"] = np.where(long_ok, 1, np.where(short_ok, -1, 0))

    return f[["open", "high", "low", "close", "signal", "atr"]].copy()

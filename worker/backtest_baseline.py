"""Baselines (§7.2) — icke-förhandlingsbart. Utan dessa går det inte att skilja
EDGE från BTC-BETA. Tre nollor att slå:

  1. Buy & hold BTC
  2. Buy & hold universumet (likaviktat)
  3. Slumpmässig entry MED SAMMA riskhantering (samma stop/TP/storlek; bara
     signalen är myntsingel)

Slår strategin inte #3 är "edgen" bara stop-loss-logiken + beta, inte signalerna.
"""
import numpy as np
import pandas as pd

import backtest_engine
import backtest_metrics


def buy_hold_equity(close: pd.Series, initial: float) -> pd.Series:
    close = close.dropna()
    if close.empty:
        return pd.Series(dtype=float)
    return (initial * close / close.iloc[0]).rename("equity")


def buy_hold_universe_equity(data: dict, initial: float) -> pd.Series:
    """Likaviktad köp-och-håll över alla coins (normaliserade, forward-fill)."""
    norm = []
    for df in data.values():
        c = df["close"].dropna()
        if not c.empty:
            norm.append(c / c.iloc[0])
    if not norm:
        return pd.Series(dtype=float)
    panel = pd.concat(norm, axis=1).sort_index().ffill()
    return (initial * panel.mean(axis=1)).rename("equity")


def random_metrics(data: dict, cfg, timeframe: str, n_seeds: int = 20) -> dict:
    """Samma motor + riskhantering, men slumpmässig signal. Snittas över seeds.

    Entry-frekvensen matchar strategins (samma andel icke-noll-signaler per coin)
    så jämförelsen är rättvis — samma antal "chanser", bara slumpmässig riktning.
    """
    per_symbol_rate = {
        s: float((df["signal"] != 0).mean()) for s, df in data.items()
    }
    metric_runs = []
    for seed in range(1, n_seeds + 1):
        rng = np.random.RandomState(seed)
        data_rng = {}
        for s, df in data.items():
            rate = per_symbol_rate[s]
            u = rng.random(len(df))
            direction = rng.choice([-1, 1], size=len(df))
            sig = np.where(u < rate, direction, 0)
            # Behåll allt utom signalen → identisk riskhantering, bara entry är slump.
            data_rng[s] = df.assign(signal=sig)
        equity, trades = backtest_engine.run(data_rng, cfg)
        metric_runs.append(backtest_metrics.compute(equity, trades, timeframe))

    keys = metric_runs[0].keys()
    return {k: float(np.mean([m[k] for m in metric_runs])) for k in keys}

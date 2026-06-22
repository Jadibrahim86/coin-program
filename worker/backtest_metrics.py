"""Nyckeltal på PORTFÖLJENS equity-kurva + trade-statistik (§7.5).

Max drawdown och profit factor säger mer än win rate. Allt räknas på equity-kurvan
(inte per trade) så portfölj-effekter (samtidiga positioner) fångas korrekt.
"""
import math

import numpy as np
import pandas as pd

BARS_PER_YEAR = {"5m": 105120, "15m": 35040, "1h": 24 * 365, "4h": 6 * 365, "1d": 365}


def _max_drawdown(equity: pd.Series) -> float:
    peak = equity.cummax()
    dd = equity / peak - 1.0
    return float(dd.min()) if len(dd) else 0.0


def _longest_losing_streak(trades: list) -> int:
    longest = cur = 0
    for t in trades:
        if t["pnl"] < 0:
            cur += 1
            longest = max(longest, cur)
        else:
            cur = 0
    return longest


def compute(equity: pd.Series, trades: list, timeframe: str) -> dict:
    bars_per_year = BARS_PER_YEAR.get(timeframe, 365)
    out = {"num_trades": len(trades)}

    if len(equity) < 2 or equity.iloc[0] == 0:
        return {**out, "total_return": 0.0, "cagr": 0.0, "sharpe": 0.0,
                "sortino": 0.0, "max_drawdown": 0.0, "profit_factor": 0.0,
                "expectancy": 0.0, "win_rate": 0.0, "longest_losing_streak": 0}

    total_return = float(equity.iloc[-1] / equity.iloc[0] - 1.0)
    rets = equity.pct_change().dropna()
    mean, std = rets.mean(), rets.std()
    downside = rets[rets < 0].std()

    sharpe = float(mean / std * math.sqrt(bars_per_year)) if std and not math.isnan(std) else 0.0
    sortino = float(mean / downside * math.sqrt(bars_per_year)) if downside and not math.isnan(downside) else 0.0

    years = len(equity) / bars_per_year
    cagr = float((equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1) if years > 0 else 0.0

    pnls = np.array([t["pnl"] for t in trades], dtype=float)
    wins = pnls[pnls > 0]
    losses = pnls[pnls < 0]
    win_rate = float(len(wins) / len(pnls)) if len(pnls) else 0.0
    gross_win, gross_loss = wins.sum(), -losses.sum()
    profit_factor = float(gross_win / gross_loss) if gross_loss > 0 else (float("inf") if gross_win > 0 else 0.0)
    expectancy = float(pnls.mean()) if len(pnls) else 0.0

    return {
        **out,
        "total_return": total_return,
        "cagr": cagr,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown": _max_drawdown(equity),
        "profit_factor": profit_factor,
        "expectancy": expectancy,
        "win_rate": win_rate,
        "longest_losing_streak": _longest_losing_streak(trades),
    }


def format_comparison(strategy: dict, baselines: dict) -> str:
    """Tabell: strategi vs baselines. Edge = slår den den ska slå (särskilt random)."""
    cols = ["total_return", "cagr", "sharpe", "sortino", "max_drawdown",
            "profit_factor", "win_rate", "num_trades"]
    names = ["Strategi"] + list(baselines)
    rows = [strategy] + [baselines[b] for b in baselines]

    header = f"{'Mått':<22}" + "".join(f"{n:>16}" for n in names)
    lines = [header, "-" * len(header)]
    for c in cols:
        cells = ""
        for r in rows:
            v = r.get(c, 0.0)
            if c in ("total_return", "cagr", "max_drawdown", "win_rate"):
                cells += f"{v * 100:>15.1f}%"
            elif c == "num_trades":
                cells += f"{int(v):>16}"
            else:
                cells += f"{v:>16.2f}"
        lines.append(f"{c:<22}" + cells)
    return "\n".join(lines)

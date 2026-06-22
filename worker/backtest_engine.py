"""Event-driven backtest-motor (Fas 4 — GRINDEN).

Designprincip mot lookahead bias (#1-buggen): signalen genereras vid bar t:s CLOSE
men exekveras på bar t+1:s OPEN. Det görs genom att skifta signalen ett steg
(`signal.shift(1)`) innan motorn ser den — så ett historiskt beslut kan aldrig
använda framtida pris.

Portfölj-medveten (§7.3): ett globalt equity, max antal samtidiga positioner, och en
equity-kurva markerad bar-för-bar (för drawdown/Sharpe i metrics). Kostnader: avgift per
sida, slippage, och en FUNDING-drag (long betalar, short får — antagande, eftersom
historisk funding inte finns gratis; se PLAN.md §7.1).

Korrelationskontroll: i krypto är nästan allt högt BTC-korrelerat, så taket på antal
samtidiga positioner ÄR i praktiken korrelationstaket. Dokumenterat val, inte en miss.
"""
from collections import namedtuple
from dataclasses import dataclass

import math

import pandas as pd

Bar = namedtuple("Bar", "open high low close esig eatr")


@dataclass
class BacktestConfig:
    initial_equity: float = 10_000.0
    risk_pct: float = 0.01          # risk per trade som andel av equity
    k_atr_stop: float = 1.5         # stop = k_atr_stop * ATR
    rr_target: float = 2.0          # take profit = rr_target * stopavstånd
    fee_pct: float = 0.0005         # avgift per sida (0.05 %)
    slippage_pct: float = 0.0005    # slippage per fill
    max_positions: int = 5          # portfölj-/korrelationstak
    max_notional_pct: float = 1.0   # PER POSITION: notional <= equity * detta
    max_gross_exposure: float = 1.0 # PORTFÖLJ: total notional <= equity * detta (1.0 = ingen hävstång)
    funding_daily_pct: float = 0.0003  # antagen funding-drag per dygn (long betalar)
    allow_short: bool = True


def _prep(df: pd.DataFrame):
    """{ts: Bar} med entry-signal/ATR redan skiftade ett steg (→ exekvering t+1 open)."""
    d = df.copy()
    d["esig"] = d["signal"].shift(1)
    d["eatr"] = d["atr"].shift(1)
    bars = {}
    for ts, row in d.iterrows():
        bars[ts] = Bar(row["open"], row["high"], row["low"], row["close"], row["esig"], row["eatr"])
    return bars


def run(data: dict, cfg: BacktestConfig = BacktestConfig()):
    """data: {symbol: df med open/high/low/close/signal/atr}, indexerat på ts.

    Returnerar (equity_curve: pd.Series, trades: list[dict]).
    """
    sym_bars = {s: _prep(df) for s, df in data.items()}
    union_index = sorted({ts for bars in sym_bars.values() for ts in bars})

    cash = cfg.initial_equity
    positions = {}          # symbol -> dict
    trades = []
    equity_points = []

    def close_position(symbol, pos, exit_ts, exit_price, outcome):
        nonlocal cash
        direction = pos["direction"]
        size = pos["size"]
        gross = (exit_price - pos["entry_price"]) * size * direction
        fees = cfg.fee_pct * (pos["entry_price"] + exit_price) * size
        days_held = max((exit_ts - pos["entry_ts"]).total_seconds() / 86400.0, 0.0)
        funding = direction * cfg.funding_daily_pct * pos["entry_notional"] * days_held
        pnl = gross - fees - funding
        cash += pnl
        trades.append({
            "symbol": symbol,
            "direction": "long" if direction == 1 else "short",
            "entry_ts": pos["entry_ts"], "entry_price": pos["entry_price"],
            "exit_ts": exit_ts, "exit_price": exit_price,
            "size": size, "pnl": pnl, "outcome": outcome,
            "bars_held": pos["bars_held"],
        })

    for ts in union_index:
        # 1) Exits för öppna positioner (pessimistiskt: stop före TP i samma bar).
        for symbol in list(positions):
            bar = sym_bars[symbol].get(ts)
            if bar is None:
                continue
            pos = positions[symbol]
            pos["bars_held"] += 1
            pos["last_close"] = bar.close
            d = pos["direction"]
            exit_price = exit_outcome = None
            if d == 1:
                if bar.low <= pos["stop"]:
                    exit_price, exit_outcome = pos["stop"] * (1 - cfg.slippage_pct), "stop"
                elif bar.high >= pos["tp"]:
                    exit_price, exit_outcome = pos["tp"] * (1 - cfg.slippage_pct), "tp"
            else:
                if bar.high >= pos["stop"]:
                    exit_price, exit_outcome = pos["stop"] * (1 + cfg.slippage_pct), "stop"
                elif bar.low <= pos["tp"]:
                    exit_price, exit_outcome = pos["tp"] * (1 + cfg.slippage_pct), "tp"
            if exit_price is not None:
                close_position(symbol, pos, ts, exit_price, exit_outcome)
                del positions[symbol]

        # 2) Entries (signal från föregående bar, exekveras på denna bars open).
        for symbol in sorted(sym_bars):
            if symbol in positions or len(positions) >= cfg.max_positions:
                continue
            bar = sym_bars[symbol].get(ts)
            if bar is None:
                continue
            sig, atr = bar.esig, bar.eatr
            if sig is None or (isinstance(sig, float) and math.isnan(sig)) or sig == 0:
                continue
            if sig == -1 and not cfg.allow_short:
                continue
            if atr is None or (isinstance(atr, float) and math.isnan(atr)) or atr <= 0:
                continue

            direction = int(sig)
            entry = bar.open * (1 + cfg.slippage_pct * direction)
            stop_dist = cfg.k_atr_stop * atr
            if stop_dist <= 0:
                continue
            equity = cash + _unrealized(positions, sym_bars, ts)
            # Portfölj-gross-tak: total notional över alla positioner <= equity * max_gross.
            current_gross = sum(p["entry_notional"] for p in positions.values())
            remaining_gross = equity * cfg.max_gross_exposure - current_gross
            if remaining_gross <= 0:
                continue
            size = (cfg.risk_pct * equity) / stop_dist
            # Per-position-tak OCH kvarvarande portfölj-gross-budget.
            max_notional = min(equity * cfg.max_notional_pct, remaining_gross)
            if size * entry > max_notional:
                size = max_notional / entry
            if size <= 0:
                continue
            stop = entry - direction * stop_dist
            tp = entry + direction * cfg.rr_target * stop_dist
            positions[symbol] = {
                "direction": direction, "entry_price": entry, "size": size,
                "stop": stop, "tp": tp, "entry_ts": ts,
                "entry_notional": size * entry, "last_close": bar.close, "bars_held": 0,
            }

        equity_points.append((ts, cash + _unrealized(positions, sym_bars, ts)))

    # 3) Stäng kvarvarande positioner på sista kända close (mark-out).
    if union_index:
        last_ts = union_index[-1]
        for symbol in list(positions):
            pos = positions[symbol]
            close_position(symbol, pos, last_ts, pos["last_close"], "eod")
            del positions[symbol]

    equity_curve = pd.Series(
        {ts: val for ts, val in equity_points}, name="equity"
    ).sort_index()
    return equity_curve, trades


def _unrealized(positions: dict, sym_bars: dict, ts) -> float:
    """Orealiserad PnL för öppna positioner vid denna tidpunkt (carry-forward close)."""
    total = 0.0
    for symbol, pos in positions.items():
        bar = sym_bars[symbol].get(ts)
        last_close = bar.close if bar is not None else pos["last_close"]
        total += (last_close - pos["entry_price"]) * pos["size"] * pos["direction"]
    return total

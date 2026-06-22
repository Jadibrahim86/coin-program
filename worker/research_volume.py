"""Forskning: testar volym-spik-före-pris-signalen på kort timeframe.

Hämtar data LOKALT via ccxt (ingen DB-skrivning — vi bloatar inte Supabase under
research). Kör long-only backtest med REALISTISKA kostnader (x1/x2/x3) + jämför baseline.
"""
import sys

import ccxt
import pandas as pd

import backtest_baseline as baseline
import backtest_engine
import backtest_metrics as metrics
import config
import signals_volume

TF = "15m"
SINCE = "2025-06-01T00:00:00Z"   # ~12–13 mån (recent regim; hård long-only-miljö)


def _fetch(ex, symbol, timeframe, since_ms, limit=1000):
    out, interval = [], config.TIMEFRAME_SECONDS[timeframe] * 1000
    while True:
        batch = ex.fetch_ohlcv(symbol, timeframe, since=since_ms, limit=limit)
        if not batch:
            break
        if out and batch[0][0] <= out[-1][0]:
            batch = [b for b in batch if b[0] > out[-1][0]]
            if not batch:
                break
        out.extend(batch)
        since_ms = batch[-1][0] + interval
        if len(batch) < limit:
            break
    return out


def _to_df(bars):
    idx = pd.to_datetime([b[0] for b in bars], unit="ms", utc=True)
    return pd.DataFrame(
        {"open": [b[1] for b in bars], "high": [b[2] for b in bars],
         "low": [b[3] for b in bars], "close": [b[4] for b in bars],
         "volume": [b[5] for b in bars]},
        index=idx,
    )


def main():
    ex = ccxt.binance({"enableRateLimit": True})
    ex.load_markets()
    since = ex.parse8601(SINCE)

    print(f"Hämtar {TF} från {SINCE[:10]} ...")
    signaled = {}
    for coin in config.UNIVERSE:
        if coin.spot not in ex.markets:
            continue
        bars = _fetch(ex, coin.spot, TF, since)
        if len(bars) < 500:
            continue
        signaled[coin.symbol] = signals_volume.generate(_to_df(bars))
    print(f"  {len(signaled)} coins, ~{len(next(iter(signaled.values())))} bars var\n")

    print("=== Volym-spik long-only, max_gross=1.0, kostnadskänslighet ===")
    for mult in (1, 2, 3):
        cfg = backtest_engine.BacktestConfig(
            max_gross_exposure=1.0, allow_short=False,
            fee_pct=0.0005 * mult, slippage_pct=0.0005 * mult,
        )
        eq, tr = backtest_engine.run(signaled, cfg)
        m = metrics.compute(eq, tr, TF)
        hold = (sum(t["bars_held"] for t in tr) / len(tr) * 15) if tr else 0
        print(f"  kostnad x{mult}:  return {m['total_return']*100:>8.0f}%  "
              f"sharpe {m['sharpe']:>5.2f}  PF {m['profit_factor']:>4.2f}  "
              f"win {m['win_rate']*100:>4.0f}%  trades {m['num_trades']}  "
              f"medel-hold {hold:.0f} min")

    print("\n=== Baseline (samma period) ===")
    if "BTC" in signaled:
        bh = baseline.buy_hold_equity(signaled["BTC"]["close"], 10000)
        mb = metrics.compute(bh, [], TF)
        print(f"  Buy&Hold BTC:   return {mb['total_return']*100:>8.0f}%  sharpe {mb['sharpe']:.2f}")
    bhu = baseline.buy_hold_universe_equity(signaled, 10000)
    mu = metrics.compute(bhu, [], TF)
    print(f"  Buy&Hold univ:  return {mu['total_return']*100:>8.0f}%  sharpe {mu['sharpe']:.2f}")

    print("\n— Tolkning —")
    print("  PF > 1.0 vid x2 (realistisk kostnad) = första tecknet på något äkta.")
    print("  PF < 1.0 redan vid x1 = idén bär inte i den här formen.")


if __name__ == "__main__":
    main()

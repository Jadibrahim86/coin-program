"""Orkestrerar grinden: data → features/signal → motor → metrics MOT BASELINE (§7).

Två datakällor:
  --synthetic : deterministisk syntetisk prisdata (ingen Supabase behövs) — för att
                röktesta att motorn hänger ihop end-to-end.
  (default)   : läser OHLCV från databasen (kräver ifylld .env + körd ingestion).

Beslutsregel vid grinden: slår strategin baselines — särskilt "Random+risk"? Om nej,
bygg inget downstream. Edgen finns inte (än).
"""
import numpy as np
import pandas as pd

import backtest_baseline as baseline
import backtest_engine
import backtest_metrics as metrics
import config
import signals

MIN_BARS = 250  # behövs för EMA200 m.m.


def make_synthetic(n_bars=1800, timeframe="4h", seed=42) -> dict:
    """Geometrisk random walk för några fejk-coins (inkl. BTC). Deterministisk."""
    rng = np.random.RandomState(seed)
    freq = {"1h": "1h", "4h": "4h", "1d": "1D"}[timeframe]
    index = pd.date_range("2022-01-01", periods=n_bars, freq=freq, tz="UTC")
    symbols = ["BTC", "ALT1", "ALT2", "ALT3", "ALT4"]
    data = {}
    for i, s in enumerate(symbols):
        rets = rng.normal(0.0002 + 0.00005 * i, 0.012 + 0.003 * i, n_bars)
        close = 100 * np.exp(np.cumsum(rets))
        open_ = np.concatenate([[close[0]], close[:-1]])
        high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.004, n_bars)))
        low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.004, n_bars)))
        volume = rng.lognormal(10, 0.5, n_bars)
        data[s] = pd.DataFrame(
            {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
            index=index,
        )
    return data


def load_from_db(conn, timeframe: str, symbols=None) -> dict:
    import db
    coin_ids = db.load_coin_ids(conn)
    universe = [c for c in config.UNIVERSE if symbols is None or c.symbol in symbols]
    data = {}
    for coin in universe:
        cid = coin_ids.get(coin.symbol)
        if cid is None:
            continue
        df = db.load_ohlcv_df(conn, cid, timeframe)
        if len(df) >= MIN_BARS:
            data[coin.symbol] = df
        else:
            print(f"  (hoppar {coin.symbol}: {len(df)} bars < {MIN_BARS})")
    return data


def run_backtest(timeframe="4h", symbols=None, synthetic=False, save=False, conn=None) -> dict:
    cfg = backtest_engine.BacktestConfig()

    raw = make_synthetic(timeframe=timeframe) if synthetic else load_from_db(conn, timeframe, symbols)
    if not raw:
        print("Ingen data att backtesta. Kör ingestion först (eller använd --synthetic).")
        return {}

    # Samma feature-/signaldefinition som live (signals.generate -> features.compute).
    signaled = {s: signals.generate(df) for s, df in raw.items() if len(df) >= MIN_BARS}
    print(f"Backtestar {len(signaled)} coins på {timeframe} "
          f"({'syntetiskt' if synthetic else 'DB'})...")

    equity, trades = backtest_engine.run(signaled, cfg)
    strat = metrics.compute(equity, trades, timeframe)

    baselines = {}
    if "BTC" in signaled:
        bh = baseline.buy_hold_equity(signaled["BTC"]["close"], cfg.initial_equity)
        baselines["Buy&Hold BTC"] = metrics.compute(bh, [], timeframe)
    bhu = baseline.buy_hold_universe_equity(signaled, cfg.initial_equity)
    baselines["Buy&Hold univ"] = metrics.compute(bhu, [], timeframe)
    baselines["Random+risk"] = baseline.random_metrics(signaled, cfg, timeframe)

    print("\n" + metrics.format_comparison(strat, baselines))
    _verdict(strat, baselines)

    if save and conn is not None:
        import db
        idx = equity.index
        db.save_backtest_run(
            conn,
            params={"timeframe": timeframe, "cfg": cfg.__dict__,
                    "coins": list(signaled), "synthetic": synthetic},
            period_start=idx.min().date().isoformat() if len(idx) else None,
            period_end=idx.max().date().isoformat() if len(idx) else None,
            metrics=strat,
            baseline_metrics=baselines,
        )
        print("\nSparat till backtest_runs.")

    return {"strategy": strat, "baselines": baselines}


def _verdict(strat: dict, baselines: dict) -> None:
    rnd = baselines.get("Random+risk", {})
    beats_random = strat["sharpe"] > rnd.get("sharpe", 0) and \
        strat["total_return"] > rnd.get("total_return", 0)
    print("\n— Bedömning —")
    if beats_random:
        print("  Strategin slår Random+risk (Sharpe & avkastning). Försiktigt lovande —")
        print("  men kör walk-forward + out-of-sample innan något downstream byggs.")
    else:
        print("  Strategin slår INTE Random+risk. Edgen är (än) bara riskhantering + beta.")
        print("  Bygg inget downstream — iterera på signalen eller tänk om.")

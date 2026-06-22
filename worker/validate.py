"""Valideringssvit — svarar på om +4385% är äkta edge eller hägring.

Tre dödsfrågor (§7, §11 i PLAN.md):
  1. Hävstångseffekt — hur mycket av avkastningen var dold portfölj-hävstång?
  2. Regim-robusthet — funkar den varje år, eller bara i bull?
  3. Kostnadskänslighet — överlever den tunna edgen högre avgifter/slippage?

Detta är inte klassisk parameter-walk-forward (vi optimerar inga parametrar — EMA/RSI/ATR
är satta a priori). Den relevanta risken är att de fasta reglerna bara råkade passa
perioden; därför testar vi konsistens över år/regimer + robusthet, inte re-optimering.
"""
import backtest_baseline as baseline
import backtest_engine
import backtest_metrics as metrics
import signals
from backtest_run import MIN_BARS, load_from_db


def _years(equity):
    return sorted(set(equity.index.year))


def _slice_metrics(equity, trades, timeframe):
    out = {}
    for y in _years(equity):
        eq = equity[equity.index.year == y]
        tr = [t for t in trades if t["exit_ts"].year == y]
        if len(eq) >= 2:
            out[y] = metrics.compute(eq, tr, timeframe)
    return out


def run(conn, timeframe: str = "4h", allow_short: bool = True) -> None:
    raw = load_from_db(conn, timeframe)
    signaled = {s: signals.generate(df) for s, df in raw.items() if len(df) >= MIN_BARS}
    mode = "long+short" if allow_short else "LONG-ONLY (inga shorts)"
    print(f"Validerar {len(signaled)} coins på {timeframe}.  Läge: {mode}\n")

    # ---- 1) Hävstångseffekt -------------------------------------------------
    print("=== 1) Hävstångseffekt (portfölj-gross-tak) ===")
    print("  Gammalt 'resultat' lät 5 korrelerade positioner = ~5x brutto. Realistiskt = 1.0.")
    for gross in (5.0, 2.0, 1.0):
        cfg = backtest_engine.BacktestConfig(max_gross_exposure=gross, allow_short=allow_short)
        eq, tr = backtest_engine.run(signaled, cfg)
        m = metrics.compute(eq, tr, timeframe)
        print(f"  max_gross={gross:>3}:  return {m['total_return']*100:>9.0f}%  "
              f"sharpe {m['sharpe']:>5.2f}  maxDD {m['max_drawdown']*100:>5.0f}%  "
              f"trades {m['num_trades']}")

    # Realistiskt tak för resten av valideringen.
    cfg = backtest_engine.BacktestConfig(max_gross_exposure=1.0, allow_short=allow_short)
    eq, tr = backtest_engine.run(signaled, cfg)

    # ---- 2) Per år vs baselines --------------------------------------------
    print("\n=== 2) Per år (max_gross=1.0) — strategi vs buy&hold ===")
    strat_y = _slice_metrics(eq, tr, timeframe)
    btc_y, univ_y = {}, {}
    if "BTC" in signaled:
        btc_y = _slice_metrics(baseline.buy_hold_equity(signaled["BTC"]["close"], cfg.initial_equity), [], timeframe)
    univ_y = _slice_metrics(baseline.buy_hold_universe_equity(signaled, cfg.initial_equity), [], timeframe)

    print(f"  {'År':<6}{'Strat ret':>12}{'Strat Sharpe':>14}{'Strat maxDD':>13}{'BTC ret':>11}{'Univ ret':>11}")
    for y in strat_y:
        s, b, u = strat_y[y], btc_y.get(y, {}), univ_y.get(y, {})
        print(f"  {y:<6}{s['total_return']*100:>11.0f}%{s['sharpe']:>14.2f}{s['max_drawdown']*100:>12.0f}%"
              f"{b.get('total_return', 0)*100:>10.0f}%{u.get('total_return', 0)*100:>10.0f}%")

    # ---- 3) Kostnadskänslighet ---------------------------------------------
    print("\n=== 3) Kostnadskänslighet (max_gross=1.0) ===")
    print("  Edgen är tunn (PF ~1.1) → känslig för sämre fills. x1 = antagna 0.05%/0.05%.")
    for mult in (1, 2, 3):
        cfg2 = backtest_engine.BacktestConfig(
            max_gross_exposure=1.0, fee_pct=0.0005 * mult, slippage_pct=0.0005 * mult,
            allow_short=allow_short,
        )
        eq2, tr2 = backtest_engine.run(signaled, cfg2)
        m2 = metrics.compute(eq2, tr2, timeframe)
        print(f"  kostnad x{mult}:  return {m2['total_return']*100:>9.0f}%  "
              f"sharpe {m2['sharpe']:>5.2f}  PF {m2['profit_factor']:>4.2f}")

    print("\n— Läs så här —")
    print("  Om avkastningen rasar från max_gross 5→1 var mycket av den hävstång.")
    print("  Om bara bull-åren är gröna och björnåren röda → regimberoende, inte robust edge.")
    print("  Om PF faller under ~1.0 vid kostnad x2 → edgen är för tunn för att överleva verkligheten.")

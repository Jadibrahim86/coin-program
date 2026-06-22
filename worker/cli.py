"""CLI-entrypoint för datapipeline + backtest.

Exempel:
    python cli.py seed-coins
    python cli.py snapshot-universe
    python cli.py ingest-ohlcv --symbols BTC ETH
    python cli.py ingest-oi
    python cli.py compute-features --timeframe 4h
    python cli.py backtest --synthetic            # röktesta motorn utan DB
    python cli.py backtest --timeframe 4h --save  # mot DB-data, spara resultatet

Cron: ingest-ohlcv ~var 15 min, ingest-oi ~var 5–15 min, snapshot-universe 1×/dygn.
"""
import argparse
import sys

import pandas as pd

import config
import db

# Windows-konsolen är cp1252 by default → tvinga UTF-8 så å/ä/ö och symboler funkar
# i terminal, cron-loggar och filer.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


def _compute_features(conn, symbols, timeframe) -> int:
    import features

    coin_ids = db.load_coin_ids(conn)
    universe = [c for c in config.UNIVERSE if symbols is None or c.symbol in symbols]
    total = 0
    for coin in universe:
        cid = coin_ids.get(coin.symbol)
        if cid is None:
            continue
        df = db.load_ohlcv_df(conn, cid, timeframe)
        if len(df) < 200:
            print(f"  (hoppar {coin.symbol} {timeframe}: {len(df)} bars)")
            continue
        f = features.compute(df)
        rows = []
        for ts, row in f.iterrows():
            vals = {
                col: (None if pd.isna(row[col]) else float(row[col]))
                for col in features.FEATURE_COLUMNS
            }
            rows.append((cid, timeframe, ts, vals, None))
        total += db.upsert_features(conn, rows)
        print(f"  {coin.symbol} {timeframe}: {len(rows)} features")
    return total


def main() -> None:
    parser = argparse.ArgumentParser(description="Coin program — pipeline + backtest")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("seed-coins", help="Skriv startlistan till coins-tabellen")
    sub.add_parser("snapshot-universe", help="Point-in-time medlemskaps-snapshot")

    p_ohlcv = sub.add_parser("ingest-ohlcv", help="Hämta OHLCV 1h/4h/1d")
    p_ohlcv.add_argument("--symbols", nargs="*")

    p_oi = sub.add_parser("ingest-oi", help="Hämta open interest + funding")
    p_oi.add_argument("--symbols", nargs="*")

    p_feat = sub.add_parser("compute-features", help="Beräkna & spara features")
    p_feat.add_argument("--symbols", nargs="*")
    p_feat.add_argument("--timeframe", default="4h")

    p_sig = sub.add_parser("generate-signals", help="Beräkna & spara AKTUELLA setups (EJ VALIDERAD)")
    p_sig.add_argument("--timeframe", default="4h")

    p_cyc = sub.add_parser("cycle", help="Köp→sälj-livscykel (long-only) + alerts")
    p_cyc.add_argument("--timeframe", default="4h")
    p_cyc.add_argument("--no-send", action="store_true", help="Skicka inte Telegram (torrkörning)")

    p_rad = sub.add_parser("radar", help="Marknads-radar: flagga ovanliga förhållanden (bevakning, ej råd)")
    p_rad.add_argument("--timeframe", default="4h")
    p_rad.add_argument("--no-send", action="store_true", help="Skicka inte Telegram (torrkörning)")

    p_bt = sub.add_parser("backtest", help="Kör backtesten mot baseline (grinden)")
    p_bt.add_argument("--timeframe", default="4h")
    p_bt.add_argument("--symbols", nargs="*")
    p_bt.add_argument("--synthetic", action="store_true", help="Syntetisk data, ingen DB")
    p_bt.add_argument("--save", action="store_true", help="Spara till backtest_runs")

    p_val = sub.add_parser("validate", help="Valideringssvit: hävstång, per-år, kostnadskänslighet")
    p_val.add_argument("--timeframe", default="4h")
    p_val.add_argument("--long-only", action="store_true", help="Validera long-only (inga shorts)")

    sub.add_parser("telegram-chatid", help="Hitta ditt Telegram chat_id (skriv till boten först)")
    sub.add_parser("alert", help="Skicka senaste signaler till Telegram")

    args = parser.parse_args()

    # Kommandon som inte behöver databasen.
    if args.cmd == "backtest" and args.synthetic:
        import backtest_run
        backtest_run.run_backtest(timeframe=args.timeframe, synthetic=True)
        return
    if args.cmd == "telegram-chatid":
        import alerts
        ids = alerts.get_chat_ids()
        if not ids:
            print("Inga chattar hittade. Skriv ett meddelande till din bot i Telegram först, kör sen igen.")
        for cid, name in ids:
            print(f"  chat_id = {cid}  ({name})")
        return

    conn = db.get_conn()
    try:
        if args.cmd == "seed-coins":
            import universe
            universe.seed_coins(conn)
        elif args.cmd == "snapshot-universe":
            import universe
            universe.snapshot_universe(conn)
        elif args.cmd == "ingest-ohlcv":
            import ingest_ohlcv
            print(f"Totalt {ingest_ohlcv.run(conn, args.symbols)} bars upsertade.")
        elif args.cmd == "ingest-oi":
            import ingest_oi
            print(f"Totalt {ingest_oi.run(conn, args.symbols)} derivat-rader upsertade.")
        elif args.cmd == "compute-features":
            n = _compute_features(conn, args.symbols, args.timeframe)
            print(f"Totalt {n} feature-rader upsertade.")
        elif args.cmd == "generate-signals":
            import live_signals
            live_signals.generate(conn, args.timeframe)
        elif args.cmd == "alert":
            import alerts
            alerts.run(conn)
        elif args.cmd == "cycle":
            import positions
            positions.run(conn, args.timeframe, send=not args.no_send)
        elif args.cmd == "radar":
            import scout
            scout.run(conn, args.timeframe, send=not args.no_send)
        elif args.cmd == "validate":
            import validate
            validate.run(conn, args.timeframe, allow_short=not args.long_only)
        elif args.cmd == "backtest":
            import backtest_run
            backtest_run.run_backtest(
                timeframe=args.timeframe, symbols=args.symbols, save=args.save, conn=conn
            )
    finally:
        conn.close()


if __name__ == "__main__":
    main()

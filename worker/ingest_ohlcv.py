"""Hämtar OHLCV via CCXT med paginering + gapdetektering, skriver till Postgres.

Inkrementellt: börjar från senaste lagrade bar, annars från BACKFILL_START.
"""
from datetime import datetime, timezone

import ccxt

import config
import db


def _interval_ms(timeframe: str) -> int:
    return config.TIMEFRAME_SECONDS[timeframe] * 1000


def fetch_ohlcv(exchange, symbol: str, timeframe: str, since_ms: int, limit: int = 1000):
    """Paginerar framåt tills ifatt nu. Returnerar [[ts_ms, o, h, l, c, v], ...]."""
    all_bars = []
    interval = _interval_ms(timeframe)
    while True:
        batch = exchange.fetch_ohlcv(symbol, timeframe, since=since_ms, limit=limit)
        if not batch:
            break
        # CCXT kan returnera överlapp mellan sidor — klipp bort dubbletter.
        if all_bars and batch[0][0] <= all_bars[-1][0]:
            batch = [b for b in batch if b[0] > all_bars[-1][0]]
            if not batch:
                break
        all_bars.extend(batch)
        since_ms = batch[-1][0] + interval
        if len(batch) < limit:
            break
    return all_bars


def _mark_gaps(bars, timeframe: str):
    """gap_flag=True på en bar om det saknas bars mellan den och föregående."""
    interval = _interval_ms(timeframe)
    flags, prev_ts = [], None
    for b in bars:
        flags.append(prev_ts is not None and (b[0] - prev_ts) > interval)
        prev_ts = b[0]
    return flags


def ingest_one(conn, exchange, coin, coin_id: int, timeframe: str) -> int:
    last_ts = db.get_last_ohlcv_ts(conn, coin_id, timeframe)
    if last_ts is None:
        since_ms = exchange.parse8601(config.BACKFILL_START)
    else:
        since_ms = int(last_ts.timestamp() * 1000) + _interval_ms(timeframe)

    bars = fetch_ohlcv(exchange, coin.spot, timeframe, since_ms)
    if not bars and last_ts is None:
        # Vissa börser (OKX) svarar tomt när `since` ligger före tillgänglig historik
        # (nylistade coins) — hämta då senaste tillgängliga fönstret istället.
        bars = exchange.fetch_ohlcv(coin.spot, timeframe, limit=1000)
    if not bars:
        return 0

    gaps = _mark_gaps(bars, timeframe)
    rows = []
    for b, gap in zip(bars, gaps):
        ts = datetime.fromtimestamp(b[0] / 1000, tz=timezone.utc)
        rows.append(
            (coin_id, timeframe, ts, b[1], b[2], b[3], b[4], b[5], gap, config.OHLCV_EXCHANGE)
        )
    return db.upsert_ohlcv(conn, rows)


def run(conn, symbols=None) -> int:
    exchange = getattr(ccxt, config.OHLCV_EXCHANGE)({"enableRateLimit": True})
    exchange.load_markets()
    coin_ids = db.load_coin_ids(conn)
    universe = [c for c in config.UNIVERSE if symbols is None or c.symbol in symbols]

    total = 0
    for coin in universe:
        cid = coin_ids.get(coin.symbol)
        if cid is None:
            print(f"  ! {coin.symbol} saknas i coins — kör `seed-coins` först")
            continue
        if coin.spot not in exchange.markets:
            print(f"  ! {coin.symbol}: {coin.spot} finns inte på {config.OHLCV_EXCHANGE}")
            continue
        for tf in config.TIMEFRAMES:
            try:
                n = ingest_one(conn, exchange, coin, cid, tf)
                total += n
                print(f"  {coin.symbol} {tf}: +{n} bars")
            except Exception as exc:  # nätfel/börsavbrott ska inte stoppa övriga coins
                print(f"  ! {coin.symbol} {tf}: {exc}")
    return total

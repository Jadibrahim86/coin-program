"""Seedar coins-tabellen och tar point-in-time-snapshots av universum-medlemskap (§1).

Snapshot = för givet datum: uppfyllde coinen filterkriterierna? Marknadsdata från
CoinGecko (gratis). Begränsning: historisk backfill av medlemskap kräver historisk
mcap/volym — snapshots byggs framåt i tiden, ett dygn i taget.
"""
from datetime import date

import requests

import config
import db

COINGECKO = "https://api.coingecko.com/api/v3"


def seed_coins(conn) -> None:
    for coin in config.UNIVERSE:
        db.upsert_coin(conn, coin)
    print(f"Seedade {len(config.UNIVERSE)} coins.")


def _fetch_market_data(ids: list[str]) -> dict:
    """mcap + 24h-volym per coingecko-id."""
    resp = requests.get(
        f"{COINGECKO}/coins/markets",
        params={
            "vs_currency": "usd",
            "ids": ",".join(ids),
            "order": "market_cap_desc",
            "per_page": 250,
            "page": 1,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return {row["id"]: row for row in resp.json()}


def snapshot_universe(conn, as_of: date | None = None) -> int:
    as_of = as_of or date.today()
    f = config.UNIVERSE_FILTER
    market = _fetch_market_data([c.coingecko_id for c in config.UNIVERSE])
    coin_ids = db.load_coin_ids(conn)

    rows = []
    for coin in config.UNIVERSE:
        m = market.get(coin.coingecko_id, {})
        mcap = m.get("market_cap") or 0
        vol = m.get("total_volume") or 0
        passed_mcap = mcap >= f.min_market_cap_usd
        passed_volume = vol >= f.min_volume_24h_usd
        # Ålder och antal börser är ännu inte kopplade — markeras som ej utvärderade.
        qualified = passed_mcap and passed_volume
        reason = {
            "market_cap_usd": mcap,
            "volume_24h_usd": vol,
            "passed_mcap": passed_mcap,
            "passed_volume": passed_volume,
            "age_evaluated": False,
            "exchanges_evaluated": False,
        }
        cid = coin_ids.get(coin.symbol)
        if cid is not None:
            rows.append((cid, as_of, qualified, mcap, vol, reason))

    n = db.insert_universe_snapshot(conn, rows)
    passed = sum(1 for r in rows if r[2])
    print(f"Universum-snapshot {as_of}: {passed}/{len(rows)} kvalificerade.")
    return n

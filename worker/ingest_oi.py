"""Open interest + funding, aggregerat över venues (§3.2). Snapshots framåt i tiden.

OBS: historisk OI-backfill kräver betald källa (Coinglass). Detta tar bara
LÖPANDE snapshots från de futures-börser CCXT stödjer gratis. Kör på schema.
"""
from datetime import datetime, timezone

import ccxt

import config
import db

# Venues vi aggregerar OI/funding från (CCXT-id). Lägg till fler vid behov.
OI_VENUES = ["binance", "bybit", "okx"]


def _build_venues() -> dict:
    """Instansiera och ladda marknader en gång (load_markets är dyrt)."""
    out = {}
    for venue in OI_VENUES:
        try:
            ex = getattr(ccxt, venue)(
                {"enableRateLimit": True, "options": {"defaultType": "swap"}}
            )
            ex.load_markets()
            out[venue] = ex
        except Exception as exc:
            print(f"  ! kunde inte ladda {venue}: {exc}")
    return out


def fetch_oi_funding(coin, venues: dict):
    """Summerar OI (USD) över venues, snittar funding. Returnerar dict eller None."""
    if not coin.perp:
        return None
    total_oi_usd = 0.0
    fundings, breakdown = [], {}
    for venue, ex in venues.items():
        if coin.perp not in ex.markets:
            continue
        try:
            oi = ex.fetch_open_interest(coin.perp)
            oi_usd = oi.get("openInterestValue")  # USD-värde där det finns
            if oi_usd:
                total_oi_usd += oi_usd
                breakdown[venue] = oi_usd
            fr = ex.fetch_funding_rate(coin.perp)
            if fr.get("fundingRate") is not None:
                fundings.append(fr["fundingRate"])
        except Exception as exc:
            breakdown[venue] = f"err: {exc}"
    if total_oi_usd == 0 and not fundings:
        return None
    avg_funding = sum(fundings) / len(fundings) if fundings else None
    return {"open_interest": total_oi_usd or None, "funding_rate": avg_funding, "breakdown": breakdown}


def run(conn, symbols=None) -> int:
    venues = _build_venues()
    if not venues:
        print("  ! inga venues tillgängliga")
        return 0
    coin_ids = db.load_coin_ids(conn)
    universe = [
        c for c in config.UNIVERSE
        if c.perp and (symbols is None or c.symbol in symbols)
    ]
    ts = datetime.now(timezone.utc)

    rows = []
    for coin in universe:
        cid = coin_ids.get(coin.symbol)
        if cid is None:
            continue
        data = fetch_oi_funding(coin, venues)
        if data is None:
            print(f"  {coin.symbol}: ingen OI/funding")
            continue
        rows.append((cid, ts, data["open_interest"], data["funding_rate"], None, data["breakdown"]))
        oi_str = f"${data['open_interest']:,.0f}" if data["open_interest"] else "n/a"
        print(f"  {coin.symbol}: OI {oi_str}  funding {data['funding_rate']}")
    return db.upsert_derivatives(conn, rows)

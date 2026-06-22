"""Konfiguration: universum, timeframes, filtertrösklar, börsval.

Allt annat läser härifrån så att backtest och live delar exakt samma definitioner.
"""
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

# Postgres-anslutning (Supabase -> Project Settings -> Database -> Connection string).
DATABASE_URL = os.environ.get("DATABASE_URL")

# Primär börs för OHLCV (CCXT-id). Binance har bredast/längst historik.
OHLCV_EXCHANGE = os.environ.get("OHLCV_EXCHANGE", "binance")

# Timeframes vi lagrar (CCXT-notation).
TIMEFRAMES = ["1h", "4h", "1d"]
TIMEFRAME_SECONDS = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}

# Hur långt bak vi backfillar vid första körningen.
BACKFILL_START = os.environ.get("BACKFILL_START", "2021-01-01T00:00:00Z")


@dataclass(frozen=True)
class UniverseFilter:
    """Filtertrösklar för point-in-time-medlemskap (§1 i PLAN.md)."""
    min_market_cap_usd: float = 500_000_000
    min_volume_24h_usd: float = 50_000_000
    min_exchanges: int = 2
    min_age_days: int = 365


UNIVERSE_FILTER = UniverseFilter()


@dataclass(frozen=True)
class Coin:
    symbol: str
    name: str
    sector: str
    coingecko_id: str
    spot: str                       # CCXT spot-symbol för OHLCV
    perp: str | None = None         # CCXT perp-symbol för derivatdata (None = saknas)
    renamed_from: str | None = None


# Startlista (L1/L2/infrastruktur/DeFi). Filtret avgör sen medlemskap över tid.
UNIVERSE = [
    Coin("BTC",  "Bitcoin",    "L1",     "bitcoin",                  "BTC/USDT",  "BTC/USDT:USDT"),
    Coin("ETH",  "Ethereum",   "L1",     "ethereum",                 "ETH/USDT",  "ETH/USDT:USDT"),
    Coin("SOL",  "Solana",     "L1",     "solana",                   "SOL/USDT",  "SOL/USDT:USDT"),
    Coin("ADA",  "Cardano",    "L1",     "cardano",                  "ADA/USDT",  "ADA/USDT:USDT"),
    Coin("XRP",  "XRP",        "payments","ripple",                  "XRP/USDT",  "XRP/USDT:USDT"),
    Coin("AVAX", "Avalanche",  "L1",     "avalanche-2",              "AVAX/USDT", "AVAX/USDT:USDT"),
    Coin("BNB",  "BNB",        "L1",     "binancecoin",              "BNB/USDT",  "BNB/USDT:USDT"),
    Coin("LINK", "Chainlink",  "oracle", "chainlink",                "LINK/USDT", "LINK/USDT:USDT"),
    Coin("DOT",  "Polkadot",   "L1",     "polkadot",                 "DOT/USDT",  "DOT/USDT:USDT"),
    Coin("POL",  "Polygon",    "L2",     "polygon-ecosystem-token",  "POL/USDT",  "POL/USDT:USDT", renamed_from="MATIC"),
    Coin("ATOM", "Cosmos",     "L1",     "cosmos",                   "ATOM/USDT", "ATOM/USDT:USDT"),
    Coin("NEAR", "NEAR",       "L1",     "near",                     "NEAR/USDT", "NEAR/USDT:USDT"),
    Coin("ARB",  "Arbitrum",   "L2",     "arbitrum",                 "ARB/USDT",  "ARB/USDT:USDT"),
    Coin("OP",   "Optimism",   "L2",     "optimism",                 "OP/USDT",   "OP/USDT:USDT"),
    Coin("INJ",  "Injective",  "DeFi",   "injective-protocol",       "INJ/USDT",  "INJ/USDT:USDT"),
    Coin("LTC",  "Litecoin",   "payments","litecoin",                "LTC/USDT",  "LTC/USDT:USDT"),
    Coin("UNI",  "Uniswap",    "DeFi",   "uniswap",                  "UNI/USDT",  "UNI/USDT:USDT"),
    Coin("AAVE", "Aave",       "DeFi",   "aave",                     "AAVE/USDT", "AAVE/USDT:USDT"),
]

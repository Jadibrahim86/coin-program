"""Konfiguration: universum, timeframes, filtertrösklar, börsval.

Allt annat läser härifrån så att backtest och live delar exakt samma definitioner.
"""
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

# Postgres-anslutning (Supabase -> Project Settings -> Database -> Connection string).
DATABASE_URL = os.environ.get("DATABASE_URL")

# Primär börs för OHLCV (CCXT-id). OKX — användaren handlar där; datakällan ska
# matcha handelsplatsen (blanda ALDRIG börsers volym i samma baslinje).
OHLCV_EXCHANGE = os.environ.get("OHLCV_EXCHANGE", "okx")

# Timeframes vi lagrar (CCXT-notation).
TIMEFRAMES = ["1h", "4h", "1d"]
TIMEFRAME_SECONDS = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}

# Hur långt bak vi backfillar coins UTAN data. Default: ~90 dagar (radarn behöver
# bara några veckor; kort backfill håller Supabase gratis-nivån). Befintliga coins
# berörs inte (inkrementell hämtning). Sätt BACKFILL_START i .env för djup historik.
def _default_backfill() -> str:
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%dT00:00:00Z")

BACKFILL_START = os.environ.get("BACKFILL_START") or _default_backfill()


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
    # Utökning 2026-07-19: halal-godkända (PiF "Comfortable") med OKX-stöd.
    # Exkluderat: memecoins (DOGE/SHIB), guld (XAUT/PAXG), stables/wrappers,
    # samt DEL/XMR/TON/DEXE/XDC/FTM som saknas på OKX.
    Coin("TRX",  "TRON",         "L1",       "tron",                 "TRX/USDT",  "TRX/USDT:USDT"),
    Coin("ZEC",  "Zcash",        "privacy",  "zcash",                "ZEC/USDT",  "ZEC/USDT:USDT"),
    Coin("XLM",  "Stellar",      "payments", "stellar",              "XLM/USDT",  "XLM/USDT:USDT"),
    Coin("BCH",  "Bitcoin Cash", "payments", "bitcoin-cash",         "BCH/USDT",  "BCH/USDT:USDT"),
    Coin("SUI",  "Sui",          "L1",       "sui",                  "SUI/USDT",  "SUI/USDT:USDT"),
    Coin("HBAR", "Hedera",       "L1",       "hedera-hashgraph",     "HBAR/USDT", "HBAR/USDT:USDT"),
    Coin("CRO",  "Cronos",       "L1",       "crypto-com-chain",     "CRO/USDT",  "CRO/USDT:USDT"),
    Coin("TAO",  "Bittensor",    "AI",       "bittensor",            "TAO/USDT",  "TAO/USDT:USDT"),
    Coin("RAY",  "Raydium",      "DeFi",     "raydium",              "RAY/USDT",  "RAY/USDT:USDT"),
    Coin("LEO",  "UNUS SED LEO", "exchange", "leo-token",            "LEO/USDT",  None),
]

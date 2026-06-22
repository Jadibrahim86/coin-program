# Coin program

Crypto swing-trading signalsystem. Se [PLAN.md](PLAN.md) för helheten och
arkitekturen. Detta repo är just nu i **Fas 0–1**: databasschema + Python-ingestion
av OHLCV och open interest.

```
coin program/
├── PLAN.md            # planen (v2, omskopad efter granskning)
├── db/
│   └── schema.sql     # Postgres/Supabase-schema (Fas 0–1 aktivt + senare faser)
└── worker/            # Python-datapipeline (ingestion)
    ├── config.py      # universum, timeframes, filtertrösklar
    ├── db.py          # Postgres-anslutning + upserts
    ├── universe.py    # seed coins + point-in-time medlemskaps-snapshots
    ├── ingest_ohlcv.py# OHLCV via CCXT + gapdetektering
    ├── ingest_oi.py   # open interest + funding, aggregerat över venues
    └── cli.py         # entrypoint (cron)
```

## Förutsättningar
- **Python 3.11+** — är *inte* installerat på den här maskinen ännu.
  Installera från [python.org](https://www.python.org/downloads/) eller Microsoft Store
  (bocka i "Add python.exe to PATH").
- En **Supabase**-databas (Postgres).

## Setup
1. Installera Python 3.11+.
2. Skapa tabellerna: kör innehållet i [db/schema.sql](db/schema.sql) i Supabase SQL Editor
   (eller `psql "$DATABASE_URL" -f db/schema.sql`).
3. Kopiera `.env.example` → `.env` och fyll i `DATABASE_URL`
   (Supabase → Project Settings → Database → Connection string).
4. Installera beroenden:
   ```powershell
   cd worker
   python -m venv .venv
   .venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```

## Köra
```powershell
cd worker
# --- Data (Fas 0–1) ---
python cli.py seed-coins          # skriv startlistan till coins
python cli.py snapshot-universe   # point-in-time medlemskaps-snapshot (CoinGecko)
python cli.py ingest-ohlcv        # OHLCV 1h/4h/1d för hela universumet
python cli.py ingest-oi           # open interest + funding (perp-coins)
# --- Features + grinden (Fas 2–4) ---
python cli.py compute-features --timeframe 4h   # beräkna & spara features
python cli.py backtest --synthetic              # RÖKTESTA motorn utan DB
python cli.py backtest --timeframe 4h --save    # backtest mot DB-data + spara
```
Begränsa till vissa coins: `python cli.py ingest-ohlcv --symbols BTC ETH`

**Börja med `backtest --synthetic`** — det kör hela kedjan (features → signal →
event-driven motor → metrics mot baseline) på syntetisk data, så du kan verifiera att
motorn hänger ihop innan Supabase/ingestion är på plats.

## Schemaläggning
Kör på schema (Windows Task Scheduler, VPS-cron, eller Supabase Edge Function):
`ingest-ohlcv` ~var 15:e min, `ingest-oi` ~var 5–15:e min, `snapshot-universe` 1×/dygn.

## Medvetna begränsningar (se PLAN.md)
- **Historisk OI** backfillas inte gratis — `ingest-oi` tar löpande snapshots framåt.
  Aggregerad historisk OI kräver betald källa (Coinglass).
- **Point-in-time-medlemskap** byggs framåt i tiden. Historisk backfill kräver historisk
  mcap/volym.
- `snapshot-universe` utvärderar mcap + volym; ålder och antal börser är ännu inte kopplade.

## Grinden (Fas 2–4) — byggd
Features ([features.py](worker/features.py)), minimal signal ([signals.py](worker/signals.py))
och en event-driven backtester ([backtest_engine.py](worker/backtest_engine.py)) med
avgifter/funding/slippage, portfölj-equity-kurva och **baseline-jämförelse**
([backtest_baseline.py](worker/backtest_baseline.py)): buy&hold BTC, buy&hold universum,
och slumpmässig entry med samma riskhantering.

**Beslutsregeln:** slår strategin baselines (särskilt Random+risk)? Om nej — bygg inget
downstream (dashboard, signal-zoo, adaptivt lager). Edgen finns inte än.

> **Status:** Python 3.12 är installerat i `worker/.venv`. `backtest --synthetic` är
> **körd och verifierad** — hela kedjan (features → signal → motor → metrics → baseline)
> kör end-to-end utan fel. Nästa steg: riktig data (Supabase + ingestion) och sedan
> **walk-forward + out-of-sample** (§7.4) för den *riktiga* grinden.

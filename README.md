# Coin program

Personligt beslutsstöd för swing-trading i krypto. Bevakar marknaden och dina
egna innehav, och pingar Telegram när något ovanligt händer eller när det kan
vara läge att sälja.

**Det ger inte köpråd och handlar inte åt dig.** Radarn är en strålkastare att
granska själv — den missar mer än den träffar.

> **Orientering för utveckling:** se [CLAUDE.md](CLAUDE.md) — arkitektur,
> principer, kalibreringar och konventioner.
> [PLAN.md](PLAN.md) är den ursprungliga byggplanen från juni och beskriver
> inte nuläget.

## Vad det gör

| Jobb | Vad det larmar om |
|---|---|
| **Radar** ([scout.py](worker/scout.py)) | 🟢 coin som vänder upp med volym bakom sig — möjligt köpläge, med flaggtypens **egen träffhistorik** i utskicket. Tystas i chop/risk-off, och coins du redan äger filtreras bort. |
| **Exit-vakt** ([exit_watch.py](worker/exit_watch.py)) | Dina innehav: 🔎 hälsokoll (håller grunden för köpet? rinner vinsten tillbaka? vänder marknaden också?) · ❌ stop bruten · 📉 topp som viker · 🔴 säljvolym. |
| **Marknadslarm** ([stress.py](worker/stress.py)) | När marknaden beter sig extremt: brett fall, allt rör sig ihop, vilda rörelser, likvidationskaskad. |
| **Veckorapport** ([report.py](worker/report.py)) | Söndagar: betygsätter systemets egna flaggor mot BTC. Självutvärdering, inte självberöm. |
| **Bevakningslista** ([watchlist.py](worker/watchlist.py)) | 👁 Coins du följer utan att äga. Larmar när coinets **läge ändras** — riktning, handel, derivat — inte när ett värde passerar en tröskel. |
| **Telegram-bot** ([telegram_bot.py](worker/telegram_bot.py)) | `/buy` `/sell` `/positions` `/bevaka` — du registrerar vad du köpt eller följer, boten bevakar det. |

## Telegram-kommandon

```
/buy WLD 0.34                    bevaka, volanpassad stop
/buy WLD 0.34 1000kr             + insats → P/L i kronor
/buy WLD 0.34 1000kr risk20      stoppen härleds ur din risk-gräns (−20%)
/buy WLD 0.34 0.30               egen stop-kurs
/sell WLD 0.36                   stäng bevakning
/positions  (/innehav)           innehav med P/L
/bevaka XRP                      följ ett coin du inte äger
/sluta XRP · /bevakning          sluta följa · se listan
/help                            hjälp
```

Lägg till `!` sist för att kringgå priskontrollen (typo-skyddet).

## Var det kör

- **VPS** (Ubuntu, EU-region) kör [run_pipeline.sh](run_pipeline.sh) varje timme
  via cron: `git pull` → hämta data → radar → exit-watch → stress →
  veckorapport. Se [DEPLOY_VPS.md](DEPLOY_VPS.md).
- **Telegram-boten** kör som systemd-tjänst ([coin-bot.service](coin-bot.service))
  så `/buy` får svar direkt.
- **Supabase (Postgres)** håller all data — schema i [db/schema.sql](db/schema.sql).
- Kod uppdateras genom `git push`; VPS:en hämtar själv nästa timme.

GitHub Actions är avvecklat ([DEPLOY.md](DEPLOY.md) sparas som referens).

## Struktur

```
coin program/
├── CLAUDE.md           # orientering: arkitektur, principer, konventioner
├── PLAN.md             # ursprunglig byggplan (historisk)
├── run_pipeline.sh     # VPS-pulsen (cron, varje timme)
├── coin-bot.service    # systemd-enhet för Telegram-boten
├── db/schema.sql       # Postgres/Supabase-schema
└── worker/
    ├── config.py           # universum (44 coins), timeframes, börsval
    ├── cli.py              # entrypoint för alla jobb
    ├── db.py               # Postgres + idempotenta upserts
    ├── features.py         # rena feature-funktioner (delas live/backtest)
    ├── ingest_ohlcv.py     # OHLCV via CCXT (OKX)
    ├── ingest_oi.py        # open interest + funding (binance+bybit+okx)
    ├── scout.py            # radarn
    ├── exit_watch.py       # exit-vakten
    ├── stress.py           # marknadslarm
    ├── report.py           # veckorapport
    ├── telegram_bot.py     # kommandolyssnare (daemon)
    ├── alerts.py           # Telegram-utskick
    └── ...                 # backtest-/forskningsspåret, se nedan
```

## Universum

44 coins, halal-filtrerade (PiF-grönlista), inga memecoins, alla på OKX, med
tillräcklig **uppmätt** dagsvolatilitet för swing och perp på minst en OI-börs.
Urvalsreglerna och varför enskilda coins uteslutits står i kommentarerna i
[config.py](worker/config.py).

## Backtest-spåret — grinden är inte passerad

Den ursprungliga strategi-idén (EMA/RSI/ATR-signal) skulle bevisa en edge mot
baseline innan något byggdes ovanpå. **Den har inte klarat walk-forward /
out-of-sample.** Därför är [live_signals.py](worker/live_signals.py),
[positions.py](worker/positions.py) och `cli.py alert` märkta **EJ VALIDERAD**
och ingår inte i produktionspipelinen.

Radar-spåret uppstod som ersättning — det påstår sig inte ha en edge.

```powershell
python cli.py backtest --synthetic              # röktest av motorn, ingen DB
python cli.py backtest --timeframe 4h --save    # mot DB-data
python cli.py validate --timeframe 4h           # hävstång, per år, kostnadskänslighet
```

## Köra lokalt (Windows)

```powershell
cd worker
.venv\Scripts\Activate.ps1
python cli.py radar --timeframe 1h --no-send
python cli.py exit-watch --no-send
python cli.py stress --no-send
python cli.py weekly-report --no-send --force
```

**Använd alltid `--no-send` vid testning** — annars går larmet till riktig telefon.

Setup från noll: Python 3.11+, kör [db/schema.sql](db/schema.sql) i Supabase,
kopiera `.env.example` → `.env`, sedan
`python -m venv .venv && pip install -r worker/requirements.txt` och
`python cli.py seed-coins`.

## Medvetna begränsningar

- **Historisk OI** backfillas inte — bara löpande snapshots framåt. Aggregerad
  historik kräver betald källa (Coinglass).
- **90 dygns default-backfill** för nya coins, för att hålla Supabase på
  gratisnivån. Sätt `BACKFILL_START` i `.env` för djupare historik.
- **Point-in-time-medlemskap** byggs framåt i tiden; `snapshot-universe`
  utvärderar mcap + volym, men ålder och antal börser är inte kopplade än.
- Trösklarna är kalibrerade på **få observationer**. De skrivs alltid ut i
  meddelandena så att de går att fortsätta mäta.

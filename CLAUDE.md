# CLAUDE.md — orientering för Claude Code

Läs den här först. PLAN.md är den *ursprungliga byggplanen* (juni) och beskriver
inte nuläget — koden har gått långt förbi den. Den här filen beskriver vad
systemet **är idag**.

## Vad det här är

Ett personligt beslutsstöd för swing-trading i krypto. Det är **inte** en
autotrader och ger **inte** köpråd. Det gör två saker:

1. **Letar möjliga köplägen** och pingar Telegram när ett coin vänder upp med
   volym bakom sig — plus marknadslarm när hela marknaden beter sig extremt.
2. **Bevakar dina innehav** som du själv registrerar via Telegram, och larmar
   när det kan vara läge att sälja (vinsten vänder, stop bruten, topp som viker,
   säljvolym).

**Användaren går bara long och shortar aldrig.** Därför är ett fallande coin bara
intressant om han redan äger det — och då är det exit-vaktens jobb, inte radarns.
Det styr vad som får skickas (se SEND_SECTIONS nedan).

Användaren är privatperson, inte kvant. Allt som skickas ut ska gå att förstå
utan finansjargong, på svenska, och alltid säga vad det *inte* vet.

## Två spår i repot — blanda inte ihop dem

**A. Live-spåret (i produktion, används dagligen)**
`scout.py` (radar) · `exit_watch.py` (exit-vakt) · `stress.py` (marknadslarm) ·
`report.py` (veckorapport) · `telegram_bot.py` (kommandon) · `alerts.py` ·
`ingest_ohlcv.py` · `ingest_oi.py`

Det här kör skarpt på en VPS varje timme och är det användaren faktiskt märker.
Trösklarna här är kalibrerade mot verklig egen data — se "Kalibrerade
konstanter" nedan.

**B. Backtest-spåret (grinden — INTE passerad)**
`signals.py` · `backtest_engine.py` · `backtest_run.py` · `backtest_baseline.py` ·
`backtest_metrics.py` · `validate.py` · `research_*.py` · `live_signals.py` ·
`positions.py`

Detta är den ursprungliga strategi-idén (EMA/RSI/ATR-signal) som skulle bevisa
en edge mot baseline. **Den har inte klarat walk-forward/out-of-sample.** Därför
är `live_signals.py`, `positions.py` (cli `cycle`) och `alerts.run` märkta
"EJ VALIDERAD" och körs inte i produktionspipelinen. Ta aldrig bort de
märkningarna, och börja inte agera på det spåret utan att grinden passerats.

Radar-spåret (A) uppstod som ersättning för (B) just för att (B) inte höll —
radarn påstår sig inte ha en edge, den pekar bara med strålkastare.

## Vad som faktiskt kör var

**VPS** (Hetzner-liknande, Ubuntu, EU-region, `/root/coin-program`):

- `run_pipeline.sh` via cron varje timme (`5 * * * *`) → `git pull` →
  `ingest-ohlcv` → `ingest-oi` → `radar` → `exit-watch` → `stress` →
  `weekly-report` (no-op utom söndagar) → `systemctl try-restart coin-bot`.
- `coin-bot.service` (systemd) kör `telegram_bot.py` som daemon → svarar på
  `/buy`, `/sell`, `/positions` direkt.

Koden uppdateras alltså på VPS:en genom att du pushar till GitHub — pipelinen
gör `git pull` själv nästa timme och startar om boten.

**Supabase (Postgres)** — all data. Gratisnivån, därför 90 dygns default-backfill.

**GitHub Actions är avvecklat.** DEPLOY.md finns bara som historisk referens.

## Datakälla

`OHLCV_EXCHANGE` default = **okx** (`config.py`). OKX är vald för att användaren
handlar där — datakällan ska matcha handelsplatsen. **Blanda aldrig volym från
olika börser i samma baslinje**; volym-trösklarna blir meningslösa då.

OI/funding aggregeras däremot över binance + bybit + okx (`ingest_oi.py`),
eftersom OI ska spegla hela marknaden, inte en börs.

Historisk OI backfillas inte — bara löpande snapshots framåt. Aggregerad
historik kräver betald källa (Coinglass).

## Vad som skickas — och vad som medvetet tystats

Ändrat 2026-08-17 efter att användaren gick igenom en månads utskick. Bakgrunden
står i koden; kortversionen:

| Larm | Var | Skickas? |
|---|---|---|
| 🟢 Vänder upp + volym | `scout.py` | **Ja** — innehav filtreras bort |
| 🟡 Faller + volym | `scout.py` | Nej — loggas för mätning |
| 🔴 Säljvolym (radarn) | `scout.py` | Nej — loggas; dubblerade exit-vakten |
| 💰 Funding-extremer | `scout.py` | **Borttaget** som eget utskick |
| 🟠 Vinsten vänder | `exit_watch.py` | **Ja** — nytt |
| ❌ Stop bruten · 📉 Trail · 🔴 Säljvolym på innehav | `exit_watch.py` | Ja |
| 🌩️ Marknadslarm · 📊 Veckorapport | `stress.py` · `report.py` | Ja |

`scout.SEND_SECTIONS` styr vilka mönster som går ut. Alla tre klassas och loggas
fortfarande till `radar_alerts` (med `meta.sent`) så mätserien inte bryts — lägg
tillbaka `"falling"`/`"distribution"` där för att få dem igen.

Funding lever kvar som en **rad på 🟢-flaggan** (`scout.funding_line`) i stället
för ett eget utskick: en lista på coins med extrem funding gav inget för någon som
bara köper spot, och INJ låg kroniskt extrem. Märkt "ej mätt" — vi har ingen
mätning som säger att funding förutsäger något.

Det viktiga larmet är **🟠 "vinsten vänder"**. Det löser användarens största
klagomål: han låg några procent plus, positionen vände, och det *enda* larmet kom
vid stop-brottet på −7%. Orsaken var att `PROFIT_ARM = 1.06` aldrig nåddes
(CHZ/ATOM/TAO toppade under +6%). Lösningen är **inte** att sänka `PROFIT_ARM` —
det var den nivån som kapade vinnarna i juli — utan ett eget larm som kräver
*bevis*: du ligger ≥ +2% **och** volymen är ≥ 4× snittet **och** momentum viker
(volskalat). Volymkravet är det som skiljer det från juli-larmen, som gick på ren
prisrörelse och tjöt på brus.

## Universum

44 coins i `config.UNIVERSE`. Urvalsregler som gäller:

- **Halal-filtrerat** (Practical Islamic Finance-grönlista). AAVE togs bort
  2026-07-19 för att den var "Uncomfortable". Detta är ett hårt krav — föreslå
  aldrig ett coin utan att det är grönt.
- Inga memecoins (DOGE/SHIB/PEPE), inga stables/wrappers, inget guld (XAUT/PAXG).
- Måste finnas på **OKX** (flera annars intressanta coins är uteslutna just
  därför — se kommentarerna i `config.py`).
- Dagsvolatilitet ≳ 2.7% — för lugna coins duger inte för swing. **Mät den, gissa
  inte**: urvalet 2026-08-17 gjordes på 30 dygns OKX-data genom
  `features.daily_vol`, samma mått boten visar i `/buy`.
- **Perp på minst en OI-venue** (binance/bybit/okx). XCH och CSPR var listans
  volatilaste (8%/dag) men uteslöts just därför — utan perp finns ingen OI, och
  OI är vad 🟠-larmet läser.
- Rimlig storlek och ingen pågående token-migration. ZBCN uteslöts som mikrocap
  med två Zebec-tokens på halal-listan samtidigt.

LEO saknar perp (`perp=None`) → ingen OI/funding för den. SAFE har perp men
Binance/Bybit rapporterar inget USD-värde för den, så den visar "OI saknas" —
funding fungerar däremot.

Notera: OKX ger bara ~300 bars vid första hämtningen av 1h, dvs ~12.5 dygn. Det
räcker över `scout._snapshot`-kravet (250 bars), så nya coins fungerar direkt och
historiken växer timvis.

## Telegram-kommandon

```
/buy WLD 0.34                    bevaka, volanpassad stop
/buy WLD 0.34 1000kr             + insats → P/L i kronor
/buy WLD 0.34 1000kr risk20      stoppen härleds ur din risk-gräns (−20%)
/buy WLD 0.34 0.30               egen stop-kurs
/sell WLD 0.36                   stäng bevakning (pris valfritt)
/positions  (/pos, /innehav)     innehav med P/L
/help                            hjälp
```

`!` var som helst i kommandot kringgår priskontrollen (typo-skyddet).
`/kapital` är **borttaget** — insats anges per trade i stället, inte globalt.
Boten lyssnar bara på `TELEGRAM_CHAT_ID`, allt annat ignoreras.

## Kalibrerade konstanter — ändra inte lättvindigt

Trösklarna i `scout.py`, `exit_watch.py` och `stress.py` är **inte gissningar**.
De är satta mot användarens egen mätdata, och varje konstant har en kommentar
som säger när och mot vad den kalibrerades. Exempel:

- `OI_THRESHOLD = 0.02` — från fördelningen av 12 060 mätta 24h-förändringar.
- `stress.py`-trösklarna — percentiler ur 45 dygns egen historik.
- `exit_watch`-trösklarna — omgjorda 2026-07-25 efter en vecka där *alla*
  förluster landade på exakt −7% (stoppen låg i brusfältet) medan vinster
  kapades vid +2%.
- `scout`-regimfiltret — 🟢-flaggor gav −0.03% i trendande läge mot −2.5% i chop.

Om du ändrar en sådan konstant: säg vad den nya nivån bygger på, och uppdatera
kommentaren. Att bara "skruva lite" raderar mätningen som ligger bakom.

**Undantag som är ärligt märkt:** `EARLY_*` i `exit_watch.py` (🟠-larmet) är
*inte* mätt mot utfall — de kommer ur användarens uttalade preferens ("jag tar
gärna 3–5%") och ur hur CHZ/ATOM/TAO såg ut innan de vände. Veckorapporten mäter
dem nu (`early_profit`-sektionen: föll priset efter larmet eller steg det?).
Justera dem när det finns siffror, inte innan.

Mätt läge 2026-08-16 att ha i huvudet: 🟢-flaggan går **sämre än BTC** i alla tre
OI-grupper (−0.5 till −1.5% på 48h, n=26), och OI separerar ingenting. Föreslå
inte fler coins eller fler signaler som om ingången vore löst — det är den inte.

`radar_alerts.meta` loggar vad varje flagga byggde på (volym, OI, regim, pris)
just för att kunna utvärdera i efterhand — `report.py` läser det. Lägg till
meta-fält när du lägger till en signal, annars går den inte att utvärdera sen.

## Principer som återkommer i koden

- **Bevakning, inte råd.** Radarn säger "granska själv", aldrig "köp". Varje
  utskick avslutas med att den missar mer än den träffar.
- **Symmetri i larmen.** Säg aldrig "säkra vinst" till någon som ligger back —
  det var en verklig bugg (RAY, −1.1%) som fixades i 97fbfcd. Slutklämmen ska
  matcha användarens faktiska P/L, inte bara vad coinet gjort.
- **Aldrig signalera på gammal eller ostängd data.** `_is_stale()` och
  `_last_closed_idx()` i `live_signals.py` används av allt som tittar på pris.
  CCXT returnerar den pågående baren sist — den har partiell OHLCV.
- **Samma kod live och i backtest.** `features.py` är rena funktioner som båda
  vägarna anropar, så definitionerna kan aldrig glida isär.
- **Dedup på allt.** Radar 8h, funding 24h, stop-påminnelse 20h, stress 12h.
  Kronisk extrem funding (INJ) ska inte pinga varje timme.
- **Mät mot BTC, inte absolut.** Veckorapporten mäter överavkastning mot BTC så
  en bra marknadsvecka inte förväxlas med en bra signal.
- **Korrelation är en risk.** Flera alts samtidigt är ETT bet — både `/buy` och
  `stress.py` säger det uttryckligen.
- **Kausalitet.** Features på rad *t* får bara använda rader ≤ *t*.

## Konventioner

- **Allt på svenska** — kod-kommentarer, docstrings, commit-meddelanden,
  Telegram-texter. Behåll det.
- **Commit-meddelanden skrivs utan å/ä/ö** (`sondagar`, `sjalvutvardering`) för
  att undvika encoding-strul. Följ mönstret. Avsluta med
  `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- Telegram-utskick använder `parse_mode="HTML"` — `<b>`, `<i>`, `<code>`.
- Kommentarer förklarar **varför**, inte vad. Håll den tonen; koden är tät på
  rationale och det är avsiktligt.
- `cli.py` tvingar UTF-8 på stdout (Windows-konsolen är cp1252).
- Tabeller skapas idempotent (`create table if not exists` +
  `add column if not exists`) — `ensure_exit_tables()` körs vid botstart.

## Kör lokalt (Windows)

```powershell
cd worker
.venv\Scripts\Activate.ps1
python cli.py radar --timeframe 1h --no-send    # torrkörning, skickar inget
python cli.py exit-watch --no-send
python cli.py stress --no-send
python cli.py weekly-report --no-send --force
python cli.py backtest --synthetic              # röktest utan DB
```

`--no-send` finns på alla utskickande kommandon. **Använd alltid det vid
testning** — annars pingar du användarens riktiga telefon.

## Dokumentationen hålls levande av en hook

`.claude/hooks/check-docs.sh` körs som **Stop-hook** (konfigurerad i
`.claude/settings.json`). När filer under `worker/`, `db/` eller något
`.sh`/`.service`-skript ändrats **senare än** README.md och CLAUDE.md, avbryter
den avslutet en gång och ber om en genomgång av om dokumentationen blivit
inaktuell.

Jämförelsen görs på **ändringstid**, inte på om filerna är ocommittade. Annars
hade en README som legat ocommittad sedan förra veckan tystat hooken i alla
sessioner därefter — just de sessioner där ny kod behöver ny dokumentation.

Det är en **engångskoll per session** (sentinel-fil på `session_id`) och den är
tyst i alla andra lägen. Får du den: gå igenom diffen på riktigt, uppdatera det
som faktiskt blivit fel, och låt resten vara — svara att inget behövdes hellre
än att skriva om text i onödan. Avaktivera via `/hooks` eller genom att ta bort
`Stop`-blocket ur `.claude/settings.json`.

Anledningen den finns: README och PLAN.md hann glida två månader från koden och
påstod "Fas 0–1, Python inte installerat" medan systemet låg i drift på en VPS.

## Kända inaktuella dokument

Uppdatera hellre än att lita på dem:

- `PLAN.md` — ursprunglig plan, beskriver inte live-spåret alls.
- `DEPLOY_VPS.md` — i övrigt rättad, men beskriver bara `ingest` + `radar` i
  pipelinen; den kör numera även exit-watch, stress och weekly-report.
- `.env.example` — säger att `OHLCV_EXCHANGE` defaultar till binance;
  `config.py` defaultar till okx.
- `db/schema.sql` — kommentarerna delar in i "Fas 0–1 aktiva" vs "senare faser";
  holdings/bot_state/radar_alerts är i högsta grad aktiva idag.

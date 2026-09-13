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
| 🔎 Hälsokoll på innehav | `exit_watch.py` | **Ja** — max 2×/dygn och coin |
| 📉 Vinsten rinner tillbaka | `exit_watch.py` | **Ja** — del av hälsokollen, räcker ensamt |
| 🟠 Vinsten vänder | `exit_watch.py` | **Borttaget** — se nedan |
| ❌ Stop bruten · 📉 Trail · 🔴 Säljvolym på innehav | `exit_watch.py` | Ja |
| 🌩️ Marknadslarm · 📊 Veckorapport | `stress.py` · `report.py` | Ja |

`scout.SEND_SECTIONS` styr vilka mönster som går ut. Alla tre klassas och loggas
fortfarande till `radar_alerts` (med `meta.sent`) så mätserien inte bryts — lägg
tillbaka `"falling"`/`"distribution"` där för att få dem igen.

Funding lever kvar som en **rad på 🟢-flaggan** (`scout.funding_line`) i stället
för ett eget utskick: en lista på coins med extrem funding gav inget för någon som
bara köper spot, och INJ låg kroniskt extrem. Märkt "ej mätt" — vi har ingen
mätning som säger att funding förutsäger något.

## Lärdomen från 🟠 — läs den innan du bygger ett nytt larm

🟠 "vinsten vänder" byggdes 2026-08-17 och togs bort 2026-08-30. Det utlöstes
**noll gånger**, och en simulering mot alla 15 innehav visade att det aldrig
hade kunnat utlösas. Felet var ett antagande som aldrig prövades mot data: jag
krävde hög volym *samtidigt som* fallande momentum. I den här datan är de
motsatt korrelerade — när priset viker på 6h ligger volymen typiskt **under**
snittet (median 0.43×). Villkoren möttes 0 gånger av 1014 timmar i vinst.

Mekaniken testades med sju mockade fall som alla gick igenom. Det som saknades
var att köra villkoren mot **den verkliga historik larmet byggdes för**.

> **Regel härefter:** innan ett nytt larm skeppas — simulera det mot `holdings`
> och `ohlcv` och redovisa hur ofta det hade utlöst och vad det hade gett.
> Ett larm som aldrig går är värre än inget larm, för det ser ut som ett skydd.

**Samma fel hände igen 2026-09-13, trots regeln.** Hälsokollens volym-villkor
kunde aldrig bli sant: koden hade `df["volume"].iloc[max(0, i-11):i+1]` där `i`
kommer från `_last_closed_idx()` och är **negativt** (−1 eller −2). För negativa
`i` kollapsar `max(0, i-11)` till 0, så slicen blev `iloc[0:-1]` — hela
historiken (2827 barer) i stället för de senaste tolv. Kvoten blev 4.85 där den
skulle vara 0.85.

Verifieringsskriptet missade det eftersom det **skrev om logiken** med positiva
index från `get_loc()`. Det testade alltså en annan kodväg än den som skeppades.

> **Skärpning av regeln:** simuleringen måste **anropa den riktiga funktionen**,
> matad med data avkortad till tidpunkten, så index och kodväg blir identiska med
> skarpt läge. Skriv aldrig om villkoren i testskriptet.

Samma mätning underkände också själva premissen. 45 varianter av vinstskydd
testades mot 25 trades och **ingen slog användarens egna exits** — den bästa
förlorade 63 procentenheter, eftersom vinnarna (ZEC +60%, WLD +21%) dippade
djupt innan de sprang. Sänk därför inte `PROFIT_ARM` och strama inte åt trailen.

### Varför trailen aldrig utlöser på volatila coins

ETHFI 10–13 sep: toppade **+11.0%**, föll 16.5 procentenheter till −7.3%, och
inget larm gick. Orsakerna, som är strukturella och värda att förstå:

- **Stoppen** låg på −15% (2.4 × dagsvol 8%) — nio procentenheter bort ännu.
- **Trailen** kräver band = 1.5 × dagsvol = **12%** *och* att du är +3% just då.
  På ett 8%/dag-coin hinner vinsten sällan bli större än bandet, så när priset
  fallit 12% från toppen är du redan under +3%. Trailen är i praktiken utesluten
  på såna coins — inte trasig, bara omöjlig att nå.
- **Hälsokollen** hade OI som steg hela tiden (+13% vid slutet), så "pengarna
  lämnade" var falskt, och volym-villkoret var trasigt (buggen ovan).

Därför finns `PEAK_*` i `exit_watch.py`: ett eget villkor som bara tittar på din
egen topp och din egen vinst, med band = max(4%, **1.0** × dagsvol) mot trailens
1.5. Det **räcker ensamt** — de andra tre kräver två av tre. På ETHFI hade det
larmat 12 sep 23:00 medan positionen fortfarande låg **+2.0%**, med texten
"toppade +11.0%, du har tappat 9.0 procentenheter därifrån".

Verifierat mot 31 innehav / 158 innehavsdygn: 0.85 larm per coin och dygn, och
första larmet gav bättre pris än användarens faktiska exit i 15 fall mot 10.

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

**Mätning 2026-08-30 (n=93 flaggor) som styr konfluensen.** Bara två kriterier
separerar utfall, och skillnaden mellan dem är stor:

| Kriterium | Uppfyllt | Ej uppfyllt | Skillnad |
|---|---|---|---|
| Trendstyrka eff ≥ 0.55 | +3.9% (74% plus) | −1.1% (34%) | **+5.1 pp** |
| Volym ≥ 9× | +2.0% (58%) | +0.9% (50%) | +1.1 pp |
| OI ≥ +2% | +1.6% | +1.4% | +0.2 pp |
| OI ≥ +7% (gamla ✅✅) | +0.4% | +2.2% | **−1.8 pp** |
| 5d positiv | uppfyllt i 92 av 93 | — | ingen urskiljning |

Därför är stjärnorna nu **två**, inte fyra: OI och 5d var dekoration, och ✅✅
pekade åt fel håll (den infördes på n=4). OI står kvar som förklarande text.
`STRONG_TREND = 0.55`, `VOL_STRONG = 9.0`, `OI_STRONG` borttagen.

Kombinerat: stark trend + hög volym gav **+4.3% och 82% positiva** (n=22); svag
trend + låg volym gav **−2.2% och 25%** (n=20). `scout.flag_track_record()`
räknar om detta varje körning och skriver ut det i utskicket, så texten
korrigerar sig själv om mönstret ändras.

`CHOP_MAX = 0.20` släpper fortfarande igenom flaggor i spannet 0.20–0.40 som i
snitt förlorar 1.5%. De är numera märkta med sitt eget utfall i stället för att
tystas — användaren ville behålla valmöjligheten.

### Utvärderingen 2026-09-13 — fyra stjärnor, allt i klartext

Användaren sa rakt ut att han inte förstod utskicket: *"eff 0.22"*, *"n=22"* och
*"n=30"* var obegripliga, och två stjärnor kändes för tunt. Det är hans verktyg
och han fattar besluten — obegriplig utdata är en bugg, inte en smaksak.

**All jargong är översatt.** `eff 0.22` → "Marknaden vandrar mest i sidled"
(`trend_ord()`). `82% positiva (n=22)` → "slog marknaden 8 gånger av 10 · bygger
på 22 tidigare fall" (`av_tio()`). Korrelationssiffran är borta ur texten och
ersatt med vad den betyder för beslutet.

> `regime["label"]` behåller formatet `"... (eff 0.60, BTC +5.0%)"` eftersom det
> **loggas till `radar_alerts.meta` och parsas tillbaka** av
> `flag_track_record()`. Ändra aldrig det formatet — bara visningen.

**Fyra stjärnor igen, men inte de gamla fyra:**

| Stjärna | Styrka | Kommentar |
|---|---|---|
| Marknaden går åt ett håll | +5.1 pp | starkast, mätt tre gånger |
| Har inte rusat i förväg | +4.9 pp | ny (relativ styrka) |
| Volymen ovanligt hög | +1.1 pp | svag men stabil |
| Nya pengar i derivaten (OI) | +1.4 pp | **svagast, har bytt tecken** |

**Om OI-stjärnan.** Den föll på n=93 och n=46 (inverterad), men med n=127 pekar
den åt förväntat håll: OI ≥ +2% slog BTC 6 gånger av 10 mot 4 av 10 under. Att
en effekt vänder när data läggs till betyder att den är svag. Normalisering mot
varje coins egen OI-rörlighet (z-score och percentil) gav inget extra. Den är med
för att användaren uttryckligen bad om den och riktningen nu stämmer — men den är
märkt som svagast i fotnoten och ska kollas om.

**De två starkaste är inte oberoende.** 37 av 41 släpande flaggor låg i stark
trend. Men effekten håller *inom* stark trend: stark+släpar gav +5.0% (7 av 10,
n=37) mot stark+leder +0.0% (6 av 10, n=20). Det är den renaste evidensen hittills.

`bucket_of()` grupperar därför numera på **trend × släpande** i stället för
trend × volym, och `report.py` följer med. Historiken räknas om från loggen varje
körning, så inget mätvärde går förlorat av bytet.

`slapar_hitrate()` räknar fotnotens siffra ur loggen i stället för att hårdkoda
den — "mönstret som funkade (AVAX)" stod kvar i två månader efter att den traden
gått −5.2%, och en siffra som inte räknas om hinner bli fel utan att någon märker.

### Mätning 2026-09-03 (n=100) — kandidat till ett tredje kriterium

**Relativ styrka vid flaggan är den starkaste okvitterade faktorn**, och riktningen
är kontraintuitiv: coins som **släpade efter** BTC gick bäst.

| Vid flaggan | Utfall 48h | Andel plus | n |
|---|---|---|---|
| Släpade efter BTC (<0%) | **+4.4%** | 69% | 39 |
| Slog BTC något (0 till +3%) | −0.4% | 40% | 30 |
| Slog BTC klart (>+3%) | −1.0% | 48% | 31 |

Som betyg skulle det ge: 2 kriterier → 2/2 ger +4.3% (82%, n=22); **3 kriterier →
3/3 ger +6.7% (87%, n=15)**; 4 (även "tidigt i rörelsen") → 4/4 ger +9.8% (100%,
n=8, för tunt). Tre är nivån. "Tidigt" korrelerar 0.67 med relativ styrka, så det
är mest dubbelräkning — släpandet bär signalen.

**Status:** används sedan 2026-09-03 bara som **sorteringsnyckel** i `sort_key()`
plus en förklarande rad, inte som stjärna. Sortering ändrar inte betyget, inte
vilka flaggor som går ut och inte vad som loggas, så utvärderingsfönstret
störs inte. **Beslut om att göra det till ett tredje kriterium tas 13 sep.**

**OI underkändes en tredje gång.** Testat villkorat inom stark trend, där andra
faktorer visat sig framträda: OI ≥ +7% gav +1.3% (n=20), +2–7% gav +5.9% (n=12),
under +2% gav **+6.0%** (n=14). Inverterat igen. Föreslå inte en OI-stjärna —
den har nu fallit på n=4 (som ✅✅), på n=93 (okvalificerat) och på n=46
(villkorat). OI-raden står kvar som förklarande text; användaren ville behålla
den och utvärderar den 13 sep.

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

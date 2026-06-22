# Crypto Swing-Trading Signalsystem — Plan v2

En byggplan (inte koden ännu) för ett system som skannar marknaden, väger ihop flera
tekniska signaler och rangordnar de bästa swingtrade-uppläggen på 1h-, 4h- och
dygnscharts — med ett lager som följer upp och justerar sig efter utfall.

> **v2-not:** Den här versionen är omskopad efter en kritisk granskning. De viktigaste
> ändringarna jämfört med v1 står i [§13](#13-vad-som-ändrats-mot-v1). Kärnidén:
> **grinden som avgör om hela projektet är värt att fortsätta är en trovärdig backtest
> med baseline.** Bygg dit snabbast möjligt — allt annat (dashboard, signal-zoo,
> adaptivt lager) är dekoration tills den grinden är passerad.

---

## 0. Mål och ärliga förväntningar

- Det här är ett **beslutsstöd**, inte en pengamaskin. Målet är upplägg med bra
  risk/reward och hög sannolikhet — inte garanterade vinster. Inget system slår
  marknaden konsekvent efter avgifter, funding och slippage.
- "Lära sig av sina misstag" är möjligt men är planens **farligaste del**, inte den
  säkra. Naiv inlärning på för lite data leder till overfitting med extra steg. Vi
  skjuter upp det medvetet (se [§8](#8-adaptivt-lager--skjuts-upp-medvetet)).
- Detta är informationsverktyg, inte finansiell rådgivning. Riskhantering
  ([§6](#6-riskhantering-kärnan)) är kärnan — den avgör om systemet överlever.
- **Den röda tråden:** separera *edge* från *beta*. I en bullmarknad ser allt bra ut.
  Allt vi bygger ska kunna jämföras mot en baseline (se [§7.2](#72-baseline-icke-förhandlingsbart)),
  annars vet vi aldrig om signalerna gör något alls.

---

## 1. Coin-universum — med point-in-time-medlemskap

Bara coins med faktisk användning, inga memecoins. **Whitelist + automatiskt filter**,
inte en hårdkodad lista.

**Startlista (L1/L2/infrastruktur/DeFi):**
BTC, ETH, SOL, ADA, XRP, AVAX, BNB, LINK, DOT, POL (f.d. MATIC), ATOM, NEAR, ARB, OP,
INJ, LTC, UNI, AAVE.

**Automatiskt filter (håller listan ren över tid):**
- Marknadsvärde över tröskel (t.ex. > 500M USD)
- Tillräcklig likviditet (24h-volym över tröskel)
- Finns på minst 2–3 stora börser med futures-marknad (krävs för open interest)
- Ålder (minst ~12 mån handelshistorik — annars går inte backtest)
- Sektor-tagg (L1, L2, DeFi, oracle, …) för relativ styrka per sektor

**Kritisk skärpning mot survivorship bias (var planens största tysta bugg):**
Filtret ovan gäller *idag*. Att backtesta på dagens godkända lista *är* survivorship
bias per konstruktion — du testar bara på överlevarna. Lösning:

- **Point-in-time-universum.** Spara *vilka coins som uppfyllde kriterierna vid varje
  historiskt datum*. Backtesten frågar "vad fanns i universumet den 2023-04-01?", inte
  dagens lista.
- Hantera **symbolbyten, renames och delistningar** som förstklassig data, inte kantfall.
  MATIC→POL är ett levande exempel; coins som dök/dog måste finnas kvar i historiken med
  korrekt kontinuitet.
- Filtret kör periodiskt och flaggar nya kandidater + coins att plocka bort. Du godkänner
  manuellt → ingen pump-coin smyger in automatiskt.

---

## 2. Dataarkitektur

**Data vi behöver:**
- **OHLCV** per coin för 1h, 4h, daily — live + så långt bak som möjligt för backtest.
- **Derivatdata** (särskilt BTC): open interest, funding rate, long/short-ratio,
  likvidationer. **Aggregerad över venues**, inte från en enda börs (se [§3.2](#32-open-interest--derivat-bekräftelse-inte-secret-sauce)).
- **Marknadskontext**: BTC-dominans, total marknadscap, Fear & Greed (svagt filter).

**Källor:**
- Pris/OHLCV: börs-API:er via **CCXT** (Binance, Bybit, Coinbase, OKX). Aggregatorer
  (CoinGecko, CryptoCompare) för längre historik.
- OI / funding / likvidationer: **Coinglass-API** (ger aggregering), eller flera futures-API
  och aggregera själv.
- Fear & Greed: Alternative.me (gratis).

**Datakvalitet är inte valfritt.** CCXT-data har luckor, börser har avbrott, symboler byter
namn. Bygg ett **normaliserings-/gaphanteringssteg** mellan ingestion och lagring:
flagga luckor, hantera renames, validera att bars är kontinuerliga. Skräpdata in =
falsk backtest ut.

**Lagring:** Supabase (Postgres). Partitionera på tid om tabellerna växer. En
ingestion-job hämtar och sparar; **allt annat läser från databasen** så att backtest och
live använder exakt samma datakälla.

---

## 3. Signaler — färre, okorrelerade, ärligt värderade

Varje byggsten ger en **delpoäng** (t.ex. −100 till +100). De vägs ihop i [§5](#5-scoring--rankningsmotor).

> **Princip mot dold koncentration:** EMA-trend, MACD, högre-toppar/-bottnar och breakout
> mäter i praktiken *samma sak* (trend/momentum). En viktad summa av korrelerade signaler
> tror sig ha 6 röster men har egentligen 2, räknade tre gånger. **Gruppera signaler i
> okorrelerade familjer och vikta på familjenivå**, inte per indikator. MVP börjar med
> 3–4 *medvetet okorrelerade* signaler, inte hela zoot.

### 3.1 Price action (trend/momentum-familjen)
- Marknadsstruktur: högre toppar/bottnar (uptrend) vs lägre toppar/bottnar.
- Stöd/motstånd & swing-nivåer: zoner där priset vänt förut.
- Breakouts **med volymbekräftelse** (utbrott på låg volym är ofta falska).
- Indikatorer som *stöd*, inte hela strategin: EMA (20/50/200) för trend, RSI för
  momentum, MACD för momentumskifte, **ATR** för volatilitet (driver stop loss +
  positionsstorlek), Bollinger Bands för expansion/kontraktion, VWAP/volume profile.
- Candlestick-/formationsmönster som *extra* bekräftelse, aldrig ensam trigger.

### 3.2 Open interest & derivat — bekräftelse, inte secret sauce
Den klassiska 2×2-tabellen pris/OI är **överbefolkad** (en av de mest spridda figurerna
i krypto-Twitter) och är *coincident bekräftelse*, inte prediktiv edge. Behandla den som
ett **filter/bekräftelse**, inte hemligt vapen.

| Pris | OI   | Tolkning |
|------|------|----------|
| Upp  | Upp  | Ny köpkraft in — trend bekräftad |
| Upp  | Ner  | Short-covering — svagare, kan tappa fart |
| Ner  | Upp  | Ny shortkraft — nedtrend bekräftad |
| Ner  | Ner  | Long-likvidering — kan vara nära utbottning |

Två tekniska krav för att den ska vara meningsfull:
- **Aggregera OI/funding över venues.** OI från enbart Binance är inte aggregerad OI och
  vilseleder.
- **Path-beroende → lätt att smyga in lookahead.** Var extra noga i backtesten.

Plus: **funding rate** (extremlägen = överhettad positionering = reversal-risk),
**long/short-ratio**, **likvidationskluster** (drar ofta priset till sig).

### 3.3 Multi-timeframe — filter + tidig entry, INTE tre-i-rad-enighet
Paradox att undvika: **maximal confluence = maximalt sen entry.** När daily + 4h + 1h
alla pekar åt samma håll är rörelsen ofta redan mogen — du går in sent nära där stops
sitter. Bättre modell:

- **Daily = riktningsfilter** (handla bara i daily-trendens riktning).
- **4h = struktur/setup.**
- **1h = timing.** En *divergens/pullback* på 1h/4h *inom* daily-filtret ger ofta bättre
  entry än när allt redan är i lockstep.

### 3.4 Egna tillägg (lägg till *efter* att grinden passerats)
Bra idéer, men de hör till efter att en tunn version visat edge — inte i MVP:
- **Relativ styrka vs BTC**: rotera in i de coins som leder mot BTC just nu.
- **Volatilitetsregim (ATR-percentil)**: styr logik + positionsstorlek (mindre i hög vol).
- **Marknadsregim-detektor**: trend vs range (se [§4](#4-regim-detektorn-eget-avsnitt)).
- **Korrelations-/risk-on-filter**: när allt rör sig med BTC är "diversifiering" en
  illusion — minska antal samtidiga positioner.
- **Sentiment-filter (svagt)**: Fear & Greed är till stor del omdöpt pris/volatilitet.
  Liten tilt i extremlägen, ingen ingenjörsmöda.

---

## 4. Regim-detektorn (eget avsnitt — single point of failure)

Hela det adaptiva och regim-betingade lagret hänger på en **pålitlig regim-etikett**.
"Trend vs range" är notoriskt svårt: etiketten är själv laggande och kan chattra. Om
detektorn flippar sent eller skakar blir alla regim-betingade beslut skräp. Därför är den
ett eget designproblem, inte en bock i en lista.

Krav på en användbar regim-detektor:
- **Stabil** (hysteres/debouncing så den inte flippar fram och tillbaka på brus).
- **Inte laggande nog att vara värdelös** — explicit mät hur sent den byter etikett.
- **Validerad isolerat**: innan den får styra vikter, mät hur ofta dess etikett stämmer
  och hur sent den byter, separat från resten av systemet.

Möjliga ingångar: ADX/trendstyrka, ATR-percentil, Hurst-exponent, choppiness index,
auto-korrelation i avkastning. Börja enkelt och mät stabiliteten innan den kopplas in.

---

## 5. Scoring- & rankningsmotor

1. Beräkna delpoäng per coin per timeframe.
2. Väg ihop till **composite-poäng per coin** — **på familjenivå** (§3) så korrelerade
   signaler inte dubbelräknas. Vikterna är statiska i MVP; det adaptiva lagret ([§8](#8-adaptivt-lager--skjuts-upp-medvetet))
   rör dem först långt senare.
3. Lägg på riktningsfilter (daily) + regim-etikett (§4).
4. **Rangordna** alla coins → toppen = bästa swing-uppläggen just nu.
5. För varje topp-signal, generera konkret förslag:
   - Riktning (long/short), entry-zon
   - **Stop loss** (ATR-baserad, inte godtycklig)
   - 1–2 take profit-nivåer
   - Beräknad **risk/reward** (filtrera bort allt under t.ex. 1.5)
   - Confidence + **vilka signaler som triggade** (transparens — du ska förstå *varför*)

---

## 6. Riskhantering (kärnan)

- **Risk per trade**: fast % av kapitalet (1–2 %). Positionsstorlek räknas *ut från*
  avståndet till stop loss, inte tvärtom.
- **Max samtidig exponering** + max antal öppna positioner.
- **Korrelationskontroll**: tillåt inte 5 "olika" longs som egentligen är samma BTC-bet.
- **Hävstångsvarning**: OI/funding-datan frestar till hög hävstång. Systemet ska *räkna
  och visa* risk, inte uppmuntra. Likvideringar gör annars hela poängen meningslös.
- **Detta måste leva i backtesten också** (se [§7.3](#73-portfölj-inte-per-trade)) — risk­regler
  som bara finns live men inte i backtesten ger falska drawdown-siffror.

---

## 7. Backtesting — projektets grind

Detta är avsnittet som avgör om något av det andra är värt att bygga. Dra fram det
**före** dashboard och signal-zoo.

### 7.1 Event-driven, ingen lookahead
- **Event-driven backtester** som matar historisk data **bar för bar**. Aldrig
  framtidsdata in i ett historiskt beslut → lookahead bias är #1-buggen.
- Räkna in **avgifter, slippage och funding**. Funding modelleras **per trade**, inte som
  platt avgift: shorts i positiv-funding-bull *tjänar* funding, longs *betalar* — den
  asymmetrin påverkar netto-RR på fleradagars­hålltider.
- **Survivorship bias**: kör mot **point-in-time-universumet** (§1), inte dagens lista.

### 7.2 Baseline (icke-förhandlingsbart)
Mät alltid mot minst tre nollor — annars går det inte att skilja skill från beta:
1. **Buy-and-hold BTC**
2. **Buy-and-hold universumet** (likaviktat)
3. **Slumpmässig entry med samma riskhantering** (samma stop/TP/storlek, myntsingel som
   signal)

Om systemet inte slår #3 är "edgen" bara din stop-loss-logik + beta mot uppmarknad, inte
signalerna.

### 7.3 Portfölj, inte per-trade
Win rate / profit factor / expectancy *per trade* räcker inte. Backtestern bygger en
**equity-kurva som respekterar concurrency- och korrelationstaken** (§6). Sharpe/Sortino
och max drawdown räknas på portföljkurvan — annars är de fiktion.

### 7.4 Walk-forward
Optimera på en period, testa på *nästa, osedda* period, rulla framåt. Det är skillnaden
mellan ett system som funkar och ett som memorerat historik. **Varning:** med ~18 coins
och swingtrades som varar dagar är data tunn — räkna med få *oberoende* trades och var
ödmjuk inför hur lite walk-forward faktiskt kan validera (se [§8](#8-adaptivt-lager--skjuts-upp-medvetet)).

### 7.5 Nyckeltal
Profit factor, expectancy, Sharpe/Sortino, **max drawdown**, längsta förlustsvit — alla
*relativt baseline* (§7.2). Max drawdown och profit factor säger mer än win rate. Ett
system med 40 % träff och bra RR slår 70 % träff med dålig RR.

### Verktygsval (du valde Python-worker)
`ccxt` (ingestion) → Postgres/Supabase → `pandas` + `pandas-ta` (features). För
backtestern: **inte "vectorbt *eller* backtrader"** (det döljer ett verkligt val).
vectorbt är vektoriserad och snabb men gör det lätt att smyga in lookahead och svårt att
modellera path-beroende stops + per-trade funding rätt. För korrekt risk vill du ha en
**event-driven** kärna. Rekommendation: **en egen liten event-driven kärna** (logiken är
inte stor) som du till 100 % förstår och litar på — alternativt backtrader.

---

## 8. Adaptivt lager — skjuts upp *medvetet*

Det här är det du vill ha mest, och där det är lättast att förstöra ett fungerande system.
Det presenterades i v1 som det "robusta startstället" — det är fel. **Bygg det sist, på
riktig live-logg, inte på historik.**

**Steg A — Loggning (bygg detta tidigt, det är ofarligt och nödvändigt):**
Spara varje genererad signal med *alla* feature-värden + faktiskt utfall (träffade TP?
stop? hur långt rörde sig priset? regim-etikett vid signaltillfället?). Utan loggen finns
inget att lära av. Detta är det enda steget som hör hemma tidigt.

**Steg B — Adaptiv viktning (FARLIGAST — vänta länge):**
Att re-vikta på "hur bra varje signal förutsagt utfall de senaste N trades i nuvarande
regim" är **overfitting med extra steg** när N är 20–50. Signal-brus-förhållandet är
uselt; du riskerar att vrida vikt mot det som *råkade* fungera och göra systemet sämre
live. Rör inte vikterna förrän du har **hundratals oberoende live-utfall**.

**Steg C — Regim-medveten kalibrering:** Bygger helt på en pålitlig regim-detektor (§4).
Inte meningsfullt förrän §4 är validerad isolerat *och* du har data per regim.

**Steg D — Sannolikhetsmodell (valfritt, mycket senare):** Enklare ML (logistisk
regression / gradient boosting) som skattar *sannolikheten* att uppläget lyckas. Krav:
walk-forward, out-of-sample, aldrig tränas på data den sen testas på. Börja **inte** här.

**Undvik (åtminstone till en början):** Reinforcement learning / "AI som tradar fritt".
Instabilt, datahungrigt, nästan omöjligt att felsöka ensam.

**Skyddsräcken:** All adaption valideras out-of-sample innan den rör live-vikter. Tak för
hur snabbt/mycket vikter får ändras. Men kom ihåg: skyddsräcken minskar skadan, de skapar
inte data som inte finns.

---

## 9. Teknisk stack & arkitektur

Du kör redan Next.js + Supabase + TypeScript, och valde **Python-worker** för analysen.

- **Analys-/backtest-motor (Python-worker):** separat tjänst. `ccxt`, `pandas`,
  `pandas-ta`, ev. `scikit-learn` (Steg D, senare). Skriver signaler till Supabase;
  Next.js läser bara. Egen event-driven backtest-kärna (§7).
- **Databas:** Supabase (Postgres).
- **Frontend (dashboard) — byggs *efter* grinden:** Next.js. Watchlist, rangordnade
  signaler, charts (TradingView lightweight-charts), backtest-resultat **mot baseline**,
  signal-historik med utfall.
- **Schemaläggning/ingestion:** cron-driven worker (liten VPS eller Supabase Edge
  Functions) som hämtar OHLCV + OI på schema, med gaphantering (§2).
- **Alerts:** Telegram-bot / push / email när en högt rankad signal dyker upp.

---

## 10. Roadmap (omskopad — grinden först)

Vänd på v1: bygg fram till en **trovärdig backtest med baseline** innan dashboard och
signal-zoo. Den backtesten är grinden som avgör om resten är värt att bygga.

- **Fas 0** – Coin-universum (med **point-in-time-design** från start) + datakällor +
  Supabase-schema.
- **Fas 1** – Datapipeline: hämta & spara OHLCV (1h/4h/daily) + aggregerad BTC open
  interest, **med gaphantering/normalisering**.
- **Fas 2** – Beräkna **3–4 okorrelerade** features (inte hela zoot) på *en* timeframe.
- **Fas 3** – Minimal regelbaserad signalmotor (daily-filter + enkel rangordning).
- **★ Fas 4 (GRINDEN)** – Event-driven backtester: avgifter/funding/slippage,
  **portfölj-equity-kurva**, **baseline-jämförelse** (§7.2), walk-forward. **Beslut här:
  finns edge mot baseline? Om nej — stanna och tänk om, bygg inte vidare.**
- **Fas 5** – Bredda signaler (§3.4) + multi-timeframe (§3.3) *om* grinden passerades.
- **Fas 6** – Dashboard + alerts.
- **Fas 7** – Steg A-loggning live (kör en längre tid, samla utfall).
- **Fas 8** – Adaptivt lager (Steg B/C) — **endast** på hundratals riktiga live-utfall.
- **Fas 9** – (Valfritt) ML-sannolikhetsmodell (Steg D).

---

## 11. Datamodell (utkast på tabeller)

- `coins` – symbol, namn, sektor, aktiv (bool), filter-metadata, **renamed_from**
- `universe_membership` – coin_id, datum, uppfyllde_kriterier (bool) → **point-in-time** (§1)
- `ohlcv` – coin_id, timeframe, timestamp, o/h/l/c/v, **gap_flag**
- `derivatives` – coin_id, timestamp, open_interest (aggregerad), funding_rate,
  long_short_ratio
- `features` – coin_id, timeframe, timestamp, indikatorvärden (rsi, atr, ema, …),
  **regim-etikett**
- `signals` – coin_id, timestamp, riktning, composite_score, entry, stop, tp, rr,
  confidence, triggande_signaler (json), regim
- `signal_outcomes` – signal_id, utfall (tp/stop/öppen), realiserad rr, funding_betald,
  stängd_timestamp
- `weights` – signal_familj, regim, vikt, uppdaterad (driver adaptiva lagret — först Fas 8)
- `backtest_runs` – parametrar, period, nyckeltal (json), **baseline-nyckeltal (json)**

---

## 12. Vanliga fallgropar (rangordnade efter hur illa de svider)

1. **Survivorship bias inbyggd i whitelisten** – backtest på dagens överlevare. Lös med
   point-in-time-universum (§1).
2. **Ingen baseline** – kan inte skilja edge från BTC-beta. Mät mot buy-and-hold +
   slumpmässig-entry-med-riskhantering (§7.2).
3. **Per-trade- istället för portfölj-backtest** – Sharpe/drawdown blir fiktion utan
   equity-kurva som respekterar risktaken (§7.3).
4. **Lookahead bias** – framtidsdata i historiska beslut. #1 tysta buggen (§7.1).
5. **Adaptivt lager för tidigt** – re-vikta på 20–50 trades = lära sig brus (§8).
6. **Overfitting** – för många parametrar på för lite data. Färre, robusta regler vinner.
7. **Ignorerade avgifter/slippage/funding** – gör backtest till en lögn. Funding per
   trade, inte platt (§7.1).
8. **Win rate-fokus** – 40 % träff + bra RR slår 70 % träff + dålig RR.
9. **Regimblindhet & instabil regim-detektor** – samma logik i fel läge, eller en
   chattrande etikett som styr allt (§4).
10. **Korrelerade signaler dubbelräknas** – "6 signaler" som egentligen är 2 (§3).
11. **Skräpdata (luckor/renames)** – falsk backtest ut. Normalisera vid ingestion (§2).

---

## 13. Vad som ändrats mot v1

- **Roadmapen omvänd:** backtest med baseline är nu *grinden* (Fas 4), före dashboard och
  signal-zoo. Bygg inte vidare om edge mot baseline saknas.
- **Survivorship bias** flyttad från "fallgrop att minnas" till **konkret krav**:
  point-in-time-universum (§1) + ny tabell.
- **Baseline** tillagd som icke-förhandlingsbart krav (§7.2).
- **Portfölj-backtest** (equity-kurva med risktak) ersätter ren per-trade-statistik (§7.3).
- **Adaptiva lagret nedgraderat:** från "robust startställe" till "farligast, byggs sist
  på live-data" (§8). RL fortsatt avrått.
- **Regim-detektorn** fick eget avsnitt som single point of failure (§4).
- **Open interest** omklassad från secret sauce till bekräftelsefilter + krav på
  venue-aggregering (§3.2).
- **Multi-timeframe** ändrad från "tre-i-rad-enighet" till "daily-filter + tidig entry på
  pullback" för att undvika sena entries (§3.3).
- **Korrelerade signaler:** vikta på familjenivå, inte per indikator (§3, §5).
- **Funding** modelleras per trade med long/short-asymmetri, inte platt avgift (§7.1).
- **Datakvalitet/gaphantering** tillagt som eget krav (§2).
- **Stack:** Python-worker bekräftad; backtester rekommenderas som egen event-driven kärna
  snarare än "vectorbt eller backtrader" (§7).
- **MVP bantad:** 3–4 okorrelerade signaler på en timeframe, inte hela signal-zoot (Fas 2).

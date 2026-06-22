# Molnautomatik — kör hjärnan utan att din dator är på

Hjärnan körs i **GitHub Actions** (gratis) enligt schema. Då behöver varken din dator
eller någon assistent vara igång — molnet hämtar data, räknar köp/sälj och pingar din
Telegram. Schemat ligger i [.github/workflows/signals.yml](.github/workflows/signals.yml)
(var 4:e timme).

## Engångsuppsättning (~10 min)

### 1. Skapa ett GitHub-konto + repo
- Gå till [github.com](https://github.com), skapa konto om du inte har ett.
- Klicka **New repository** → namn t.ex. `coin-program` → välj **Private** → **Create**.
- **Pusha INTE `.env`** — den är redan i `.gitignore`. Hemligheterna läggs i GitHub Secrets (steg 3).

### 2. Pusha koden
I `coin program`-mappen:
```powershell
git init
git add .
git commit -m "Coin signal system"
git branch -M main
git remote add origin https://github.com/DITT-ANVÄNDARNAMN/coin-program.git
git push -u origin main
```
(Byt `DITT-ANVÄNDARNAMN`. Första pushen frågar om inloggning.)

### 3. Lägg in hemligheterna
I ditt repo på GitHub: **Settings → Secrets and variables → Actions → New repository secret**.
Skapa tre stycken (samma värden som i din `.env`):

| Namn | Värde |
|---|---|
| `DATABASE_URL` | din Supabase Session-pooler-sträng |
| `TELEGRAM_BOT_TOKEN` | din BotFather-token |
| `TELEGRAM_CHAT_ID` | `1864441006` |

### 4. Testa
- Fliken **Actions** → välj **coin-signals** → **Run workflow** (manuell körning).
- Den ska bli grön och (om köp/sälj finns) skicka en Telegram-alert.
- Efter det kör den av sig själv var 4:e timme.

## Viktig fallgrop: Binance geo-block i molnet
GitHub Actions-servrar ligger ofta på IP-adresser som **Binance blockerar**. Om
`Hämta senaste OHLCV`-steget failar med ett geo-/403-fel:
- Gå till **Settings → Secrets and variables → Actions → Variables → New variable**
- Skapa `OHLCV_EXCHANGE` = `kraken` (eller `bybit` / `okx`).
- Workflowen plockar upp den automatiskt.

(Mindre detalj: backtesten kördes på Binance-data. För live-signaler spelar exakt börs
mindre roll, men blanda inte historik från olika börser i samma tabell utan eftertanke.)

## Kostnad
Gratis. Privat repo har 2000 Actions-minuter/mån; det här jobbet drar ~3 min × 6/dygn
≈ 540 min/mån. Gott om marginal. Supabase, Telegram och allt annat är också gratisnivå.

## Stänga av / ändra takt
- **Pausa:** Actions-fliken → coin-signals → `...` → Disable workflow.
- **Ändra takt:** redigera `cron`-raden i `signals.yml` (UTC). Ex: `0 */2 * * *` = varannan timme.

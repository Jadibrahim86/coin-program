# VPS-distribution — full 18-coin-täckning + funding + valfri frekvens

GitHubs gratis-moln blockeras av Binance (geo) → bara 11 coins och ingen funding. En
liten **VPS i en Binance-vänlig region** (EU) löser allt på en gång: alla 18 coins,
funding/OI direkt från Binance, och valfri körfrekvens. ~4–5 €/mån.

> När VPS:en kör: **stäng av GitHub-workflowen** (Actions → coin-signals → ⋯ → Disable),
> annars får du dubbla alerts.

## 1. Hyr en VPS
- T.ex. **Hetzner** CX22 (~4 €/mån) eller Contabo/DigitalOcean. Välj **Ubuntu 24.04** och en
  **EU-region** (Tyskland/Finland — inte USA, så Binance funkar).
- Du får en IP-adress + rotlösenord (eller SSH-nyckel).

## 2. Logga in
Från din dator (PowerShell duger):
```powershell
ssh root@DIN-VPS-IP
```

## 3. Installera + hämta koden
Repot är privat, så använd en **token** (GitHub → Settings → Developer settings →
Personal access tokens → Fine-grained → read-only på coin-program). Kör på VPS:en:
```bash
apt update && apt install -y git python3 python3-venv python3-pip
git clone https://github.com/Jadibrahim86/coin-program.git
cd coin-program
python3 -m venv .venv
.venv/bin/pip install -r worker/requirements.txt
chmod +x run_pipeline.sh
```
(När git frågar om lösenord: klistra in din token, inte ditt GitHub-lösenord.)

## 4. Lägg in hemligheterna
Skapa `.env` på VPS:en (samma värden som i din lokala `.env`):
```bash
nano .env
```
Klistra in:
```
DATABASE_URL=<samma som i din lokala .env>
TELEGRAM_BOT_TOKEN=<samma som i din lokala .env>
TELEGRAM_CHAT_ID=<samma som i din lokala .env>
```
Spara (Ctrl+O, Enter, Ctrl+X). `OHLCV_EXCHANGE` lämnas bort → blir `binance` (alla 18 coins).

## 5. Testa en gång
```bash
./run_pipeline.sh
```
Den ska hämta data + funding och (om något är ovanligt) pinga din Telegram.

## 6. Schemalägg (varje timme)
```bash
crontab -e
```
Lägg till längst ner (byt sökväg om annan):
```
5 * * * * /root/coin-program/run_pipeline.sh >> /root/coin-program/cron.log 2>&1
```
Klart — VPS:en kör nu radarn varje timme med alla 18 coins + funding, dygnet runt.

**Vill du köra oftare än varje timme** (t.ex. var 15:e min): det kräver att vi även
hämtar 15m-data och kör radarn på 15m. Säg till så lägger jag till det — VPS:en har inga
frekvensgränser som GitHub.

## Telegram-boten (/buy /sell /positions) — engångsinstallation

Kommandolyssnaren körs som systemd-tjänst så att /buy får svar direkt:
```bash
cd ~/coin-program
cp coin-bot.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now coin-bot
systemctl status coin-bot --no-pager
```
Status ska visa **active (running)**. Testa sen i Telegram: skriv `/help` till boten.

- Tjänsten startas om automatiskt varje timme (via run_pipeline.sh) så den plockar
  upp ny kod efter git pull.
- Loggar: `journalctl -u coin-bot -n 50 --no-pager`

## Felsökning
- Inga alerts? Kolla `cat cron.log`. Tyst = inget korsade trösklarna (normalt).
- Binance-fel? Kontrollera att VPS:en är i EU (inte US-region).

#!/usr/bin/env bash
# Engångsinstallation på VPS:en. Kör EN gång efter git clone: bash setup_vps.sh
set -euo pipefail
cd "$(dirname "$0")"

apt-get update -y
apt-get install -y python3 python3-venv python3-pip
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r worker/requirements.txt
chmod +x run_pipeline.sh

echo ""
echo "==============================================================="
echo "Klart! Nästa steg:"
echo "  1. Skapa .env:   nano .env   (lägg in DATABASE_URL,"
echo "     TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID — samma som lokalt)"
echo "  2. Testa:        ./run_pipeline.sh"
echo "  3. Schemalägg:   crontab -e   (se DEPLOY_VPS.md)"
echo "==============================================================="

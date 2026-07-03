#!/usr/bin/env bash
# VPS-pulsen: hämta färsk data + funding + kör radarn. Körs av cron (se DEPLOY_VPS.md).
set -euo pipefail
cd "$(dirname "$0")"
git pull --ff-only 2>/dev/null || true   # hämta senaste kod-ändringar (ofarligt om det failar)
source .venv/bin/activate

python worker/cli.py ingest-ohlcv
python worker/cli.py ingest-oi
python worker/cli.py radar --timeframe 1h

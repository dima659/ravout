#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock.txt
printf '\nОткройте http://127.0.0.1:8000\nЭто локальное демо, не опубликованный Telegram-бот.\n\n'
DEV_MODE=true .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

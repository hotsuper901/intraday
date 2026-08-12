#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
pip install -q -r requirements.txt
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"

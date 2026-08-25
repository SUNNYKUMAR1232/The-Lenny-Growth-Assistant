#!/usr/bin/env bash
set -euo pipefail

cd /app/backend

: "${PORT:=8000}"

alembic upgrade head
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT}"

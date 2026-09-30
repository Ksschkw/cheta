#!/usr/bin/env bash
# Run Ranti locally: create the virtualenv if needed, install the runtime and
# dev dependencies, load .env, then serve the API with uvicorn.
#
# Usage:
#   bash scripts/run_local.sh
#   RANTI_HOST=127.0.0.1 RANTI_PORT=8000 RANTI_RELOAD=0 bash scripts/run_local.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV="$ROOT/.venv"
HOST="${RANTI_HOST:-0.0.0.0}"
PORT="${RANTI_PORT:-8000}"
RELOAD="${RANTI_RELOAD:-1}"

if [ ! -x "$VENV/bin/python" ]; then
    echo "[OK] creating virtualenv at $VENV"
    "$PYTHON_BIN" -m venv "$VENV"
else
    echo "[OK] reusing virtualenv at $VENV"
fi

echo "[OK] installing dependencies"
"$VENV/bin/python" -m pip install --upgrade pip
# pyproject.toml declares no build backend, so install the dependency list
# directly rather than with pip install -e ".[dev]".
"$VENV/bin/python" -m pip install \
    "fastapi>=0.115" \
    "uvicorn[standard]>=0.30" \
    "httpx>=0.27" \
    "openai>=1.40" \
    "pydantic>=2.7" \
    "memwal>=0.1.11"
"$VENV/bin/python" -m pip install \
    "pytest>=8.2" \
    "pytest-asyncio>=0.23" \
    "import-linter>=2.0" \
    "respx>=0.21"

if [ -f "$ROOT/.env" ]; then
    echo "[OK] loading $ROOT/.env"
    set -a
    # shellcheck disable=SC1091
    . "$ROOT/.env"
    set +a
else
    echo "[WARN] no .env found; copy .env.example to .env for Walrus Memory and LLM keys"
fi

if [ -z "${MEMWAL_PRIVATE_KEY:-}" ] || [ -z "${MEMWAL_ACCOUNT_ID:-}" ]; then
    echo "[WARN] Walrus Memory credentials absent; running against the offline mock client"
fi

UVICORN_ARGS=(main:app --app-dir src --host "$HOST" --port "$PORT")
if [ "$RELOAD" = "1" ]; then
    UVICORN_ARGS+=(--reload)
fi

echo "[OK] serving on http://$HOST:$PORT (web widget at /app, docs at /docs)"
exec "$VENV/bin/python" -m uvicorn "${UVICORN_ARGS[@]}"

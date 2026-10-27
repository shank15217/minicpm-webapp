#!/bin/sh
# run_daily.sh — the whole product in one command, no systemd required:
#   1) the app capture/analyze service must be up (scripts/start_app.sh)
#   2) capture+analyze every roster site (one analyze pass)
#   3) health surface: up / content / integrity per site
#   4) state.toml [state] block refreshed from the store
# systemd users (prod pvellm) get the same via minicpm-monitor.timer;
# this script is the clone-and-run path the user asked for (2026-10-25).
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${PYTHON:-.venv/bin/python}"
[ -x "$PY" ] || PY="$(command -v python3)"
APP="${APP_URL:-http://127.0.0.1:8000}"
curl -fsS --max-time 10 "$APP/health" >/dev/null 2>&1 || {
    echo "app service not answering at $APP/health - start it: scripts/start_app.sh" >&2
    exit 2
}
$PY monitorlib.py run --index baselines/index.json --base-url "$APP" \
    --db baselines/runs.db
$PY monitorlib.py status --index baselines/index.json --db baselines/runs.db
$PY scripts/refresh_state.py

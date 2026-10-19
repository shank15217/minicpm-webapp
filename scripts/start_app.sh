#!/usr/bin/env bash
# start_app.sh — run the minicpm-webapp FastAPI server via uv.
# Env overrides:
#   PORT (default 8000)  HOST (default 0.0.0.0)  BASELINES_DIR (default ./baselines)
#   LLAMA_SERVER_16X / LLAMA_SERVER_4X  (default http://127.0.0.1:8080|8081/v1/chat/completions)
#   MPW_ALLOW_TARGETS  S2 SSRF gate allowlist (comma host globs). Default
#     covers every host in the prod baseline index as of 2026-10-19; if you
#     monitor a NEW site, add it here or the gate refuses it — /health
#     .gate_lockout lists any baseline the gate would refuse (never silent).
#   MPW_ADMIN_TOKEN  when set, baseline capture requires X-Admin-Token.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
export PATH="$HOME/.local/bin:$PATH"
exec uv run uvicorn app:app --host "${HOST:-0.0.0.0}" --port "${PORT:-8000}"

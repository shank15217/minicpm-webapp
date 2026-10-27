#!/usr/bin/env bash
# start_app.sh — DEV-ONLY launcher for the minicpm-webapp FastAPI server.
# NOT the prod launcher: prod runs systemd unit deploy/systemd/minicpm-webapp.service
# (W11: the unit is the launcher; shell-scoped exports rot silently — see
# HANDOFF-minicpm-9). If you are here at 2 AM fixing prod, install the unit,
# do not adopt this script.
# Env overrides:
#   PORT (default 8000)  HOST (default 0.0.0.0)  BASELINES_DIR (default ./baselines)
#   LLAMA_SERVER_FAST / LLAMA_SERVER_DETAIL  (canonical names; legacy
#     LLAMA_SERVER_16X/_4X still shadow as fallbacks in app.py — prefer the
#     canonical pair). Defaults http://127.0.0.1:8080|8081/v1/chat/completions;
#     remote towers REQUIRE hostnames (SSRF gate refuses IP literals).
#   MPW_ALLOW_TARGETS  S2 SSRF gate allowlist (comma host globs). Default
#     covers every host in the prod baseline index as of 2026-10-19; if you
#     monitor a NEW site, add it here or the gate refuses it — /health
#     .gate_lockout lists any baseline the gate would refuse (never silent).
#   MPW_ADMIN_TOKEN  when set, baseline capture requires X-Admin-Token.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
export PATH="$HOME/.local/bin:$PATH"
# D2/D3 (clone rehearsal 2026-10-27): monitor.toml is the single config.
# Towers the user named must reach the app (default was localhost = a
# blind analyzer behind a working config), and every [[sites]] host must
# be in the SSRF allowlist (analyze gated 403 on URLs setup just captured).
if [ -f monitor.toml ]; then
    eval "$(python3 - <<'EOF'
import tomllib
cfg = tomllib.load(open("monitor.toml", "rb"))
t = cfg.get("towers", {})
detail = t.get("detail", "")
vision = t.get("vision", detail)
origin = lambda u: (u.rstrip("/")[:-3] if u.rstrip("/").endswith("/v1") else u.rstrip("/"))
if detail:
    print(f'export LLAMA_SERVER_DETAIL="{origin(detail)}"')
if vision:
    print(f'export LLAMA_SERVER_FAST="{origin(vision)}"')
hosts = ",".join(s["url"].split("//", 1)[-1].split("/", 1)[0]
                 for s in cfg.get("sites", []) if s.get("url"))
if hosts:
    print(f'export MPW_ALLOW_TARGETS="{hosts}"')
EOF
)"
fi
exec uv run uvicorn app:app --host "${HOST:-0.0.0.0}" --port "${PORT:-8000}"

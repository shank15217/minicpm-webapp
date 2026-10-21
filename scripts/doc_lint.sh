#!/usr/bin/env bash
# qa_gate clause 8 wrapper: doc-vs-code lint (see doc_lint.py for the detectors).
set -uo pipefail
cd "$(dirname "$0")/.."
PY=""; [ -x .venv/bin/python ] && PY=.venv/bin/python || PY=python3
exec "$PY" scripts/doc_lint.py "$@"

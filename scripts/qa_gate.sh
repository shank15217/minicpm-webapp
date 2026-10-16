#!/usr/bin/env bash
# qa_gate.sh — one-command QA gate (Quinn contract, QA-REPORT-6 addendum).
#
# Contract (each clause exists because its absence caused a false alarm or a
# silent hang — see MINICPM-QA-REPORT-6):
#  1. flock on a lockfile: orphaned concurrent runs contend on WAL/baseline
#     locks and were the prime suspect for the 240s "hang". Refuse to start.
#  2. Boot a HEALTHY instance (real llama backends) and a DEAD-BACKEND
#     instance (pointed at closed ports) with split BASELINES_DIR stores;
#     health-poll each with a bounded deadline; on timeout, dump instance
#     logs and fail fast — never wait on an instance that never comes up.
#  3. Suite via `uv run --extra dev --with pytest-timeout pytest --timeout=45`
#     and the WHOLE gate under `timeout 300`: a hung gate goes red WITH
#     OUTPUT; a gate that can hang is not a gate (Sammy).
#  4. All six env vars the suite reads are exported in ONE place below.
#     Half of v0.4's red gates were harness-assembly errors, not code.
#  5. EXIT trap tears down ports/processes/temp stores on every path.
set -uo pipefail
cd "$(dirname "$0")/.."

LOCK=/tmp/mpw_gate.lock
exec 9>"$LOCK"
flock -n 9 || { echo "FATAL: another qa_gate holds $LOCK (kill the orphan or wait)"; exit 1; }

HEALTHY_PORT="${HEALTHY_PORT:-8015}"
DEAD_PORT="${DEAD_PORT:-8016}"
LLAMA_16X="${LLAMA_16X:-http://127.0.0.1:8080/v1/chat/completions}"
LLAMA_4X="${LLAMA_4X:-http://127.0.0.1:8081/v1/chat/completions}"
STORE_H=$(mktemp -d); STORE_D=$(mktemp -d)
PIDS=()

cleanup() {
    local rc=$?
    for p in "${PIDS[@]:-}"; do kill "$p" 2>/dev/null; done
    fuser -k "${HEALTHY_PORT}/tcp" "${DEAD_PORT}/tcp" 8012/tcp 2>/dev/null
    rm -rf "$STORE_H" "$STORE_D" /tmp/mpw_gate_redirector_hits.log
    exit $rc
}
trap cleanup EXIT

wait_health() { # port deadline_s label
    for _ in $(seq 1 "$2"); do
        curl -sf -m 2 "http://127.0.0.1:$1/health" >/dev/null && return 0
        sleep 1
    done
    echo "FATAL: $3 on :$1 not healthy in $2 s — log tail:"
    tail -20 "/tmp/mpw_gate_$3.log" 2>/dev/null
    return 1
}

echo "== boot healthy instance :$HEALTHY_PORT"
BASELINES_DIR="$STORE_H" LLAMA_SERVER_16X="$LLAMA_16X" LLAMA_SERVER_4X="$LLAMA_4X" \
    setsid uv run --extra dev uvicorn app:app --port "$HEALTHY_PORT" \
    > "/tmp/mpw_gate_healthy.log" 2>&1 < /dev/null &
echo "== boot dead-backend instance :$DEAD_PORT"
BASELINES_DIR="$STORE_D" LLAMA_SERVER_16X="http://127.0.0.1:1/v1/chat/completions" \
    LLAMA_SERVER_4X="http://127.0.0.1:1/v1/chat/completions" \
    setsid uv run --extra dev uvicorn app:app --port "$DEAD_PORT" \
    > "/tmp/mpw_gate_dead.log" 2>&1 < /dev/null &

wait_health "$HEALTHY_PORT" 90 healthy || exit 1
wait_health "$DEAD_PORT" 90 dead || exit 1

# Redirector for the S2b per-hop-redirect pin. Must actually be RUNNING
# before REDIRECT_URL is exported — pointing a redirect test at a dead
# helper makes "connection refused" masquerade as "SSRF blocked" and fakes
# an XPASS (a defect passing for fixed). Start it, health-poll, then export.
#
# This is the GATE's own redirector, not tests/helpers/redirector.py: the
# helper suppresses its access log (log_message no-op), so it cannot serve
# as its own positive control (Sammy clause 6). This one counts hits to a
# file; the post-suite check below asserts the detector had ammunition.
uv run --extra dev python - 8012 /tmp/mpw_gate_redirector_hits.log <<'PY' &
import sys, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
hits_path, port = sys.argv[2], int(sys.argv[1])
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        with open(hits_path, "a") as f:
            f.write(self.path + "\n")
        self.send_response(302)
        self.send_header("Location", "http://127.0.0.1:8080/health")
        self.end_headers()
    def log_message(self, *a):
        pass
ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
PY
for _ in $(seq 1 15); do
    curl -sf -m2 -o /dev/null "http://127.0.0.1:8012/r" && break; sleep 1
done
: > /tmp/mpw_gate_redirector_hits.log   # gate window starts now

# (4) the vars, in one place — only the redirect-dependent ones we actually
# serve. S2 (internal target) uses its default SSRF_TEST_URL=127.0.0.1:8080/
# health against the REAL backend so the leak reproduces honestly; do NOT
# override it to a dead host.
export BASE_URL="http://127.0.0.1:${HEALTHY_PORT}"
export DEAD_BACKEND_URL="http://127.0.0.1:${DEAD_PORT}"
export WRITE_PROBES=1
export BASELINES_INDEX="$STORE_H/index.json"
export BASELINES_DIR="$STORE_H"
export REDIRECTOR=1
export REDIRECT_URL="http://127.0.0.1:8012/r"

echo "== suite (bounded: pytest --timeout=45, whole gate timeout 300)"
timeout 300 uv run --extra dev --with pytest-timeout pytest -rxX tests/ --timeout=45
suite_rc=$?
[ $suite_rc -eq 124 ] && echo "FATAL: suite hit the 300 s wall — hung gate, red with output"

# ---- clause 6: positive controls (Sammy) --------------------------------
# A test that passes on ABSENCE-of-bad-outcome can pass vacuously: a probe
# that reaches nothing looks identical to a probe that reached the target
# and found it clean (Paul's false-green S2 was exactly this). The suite's
# green only means something if the fault detectors actually fired. Assert
# the ammunition exists, mechanically, from run_history + the redirector
# hit log. Any missing -> gate red even when pytest exited 0.
echo "== clause 6: positive controls (did the fault detectors have ammunition?)"
pos_fail=0

# (6a) redirector saw >=1 request during the window -> S2/S2b exercised the
# live redirect path, not a dead port.
hits=$(wc -l < /tmp/mpw_gate_redirector_hits.log 2>/dev/null || echo 0)
if [ "${hits:-0}" -ge 1 ]; then
    echo "  PASS redirector received ${hits} request(s) (S2b path exercised)"
else
    echo "  FAIL redirector received 0 requests — S2b ran vacuously (no ammunition)"
    pos_fail=1
fi

# (6b) dead-backend store has a CHECK_FAILED/LLAMA_UNREACHABLE row -> the
# dead-instance test actually talked to the DEAD instance, not a healthy one.
uv run --extra dev python - "$STORE_D/runs.db" <<'PY'
import sqlite3, sys
try:
    c = sqlite3.connect(sys.argv[1])
    n = c.execute("SELECT COUNT(*) FROM run_history WHERE status='CHECK_FAILED'"
                  " AND error_code='LLAMA_UNREACHABLE'").fetchone()[0]
except Exception as e:
    print(f"  FAIL dead-store unreadable: {e}"); sys.exit(3)
if n >= 1:
    print(f"  PASS dead-store has {n} LLAMA_UNREACHABLE row(s) (S6 path exercised)")
    sys.exit(0)
print("  FAIL dead-store has 0 LLAMA_UNREACHABLE rows — S6 ran vacuously")
sys.exit(3)
PY
[ $? -ne 0 ] && pos_fail=1

# (6c) WRITE_PROBES actually wrote to the throwaway store -> baseline
# integrity test (S1) exercised the real write path.
uv run --extra dev python - "$STORE_H/runs.db" <<'PY'
import sqlite3, sys, os
db = sys.argv[1]
if not os.path.exists(db):
    print("  FAIL healthy throwaway store missing — WRITE_PROBES wrote nowhere"); sys.exit(3)
n = sqlite3.connect(db).execute("SELECT COUNT(*) FROM run_history").fetchone()[0]
if n >= 1:
    print(f"  PASS healthy throwaway store has {n} run_history row(s) (write path exercised)")
    sys.exit(0)
print("  FAIL healthy throwaway store empty — WRITE_PROBES ran vacuously"); sys.exit(3)
PY
[ $? -ne 0 ] && pos_fail=1

if [ "$pos_fail" -ne 0 ]; then
    echo "== gate FAILED: positive-control assertion(s) above — a fault test passed vacuously"
    exit 1
fi

# Clause 7: check-then-tag order enforced by construction. If the caller
# declares a candidate release tag, the gate ENDS with that tag's
# tag_check ritual (mark-removal checks; test tier skipped — this gate run
# already executed it, and re-running would double-count clause 6a's
# hit-log). v0.4.2 lesson: the tag was cut before tag_check ran because
# nothing made the order mechanical. Green tag_check output IS the last
# thing a release sees before `git tag`.
if [ -n "${CANDIDATE_TAG:-}" ]; then
    if [ "$suite_rc" -ne 0 ]; then
        echo "== clause 7: SKIPPED tag_check (suite rc=$suite_rc — no ritual over a red suite)"
    else
        echo "== clause 7: running tag_check ritual for candidate $CANDIDATE_TAG"
        SKIP_TESTS=1 TAG="$CANDIDATE_TAG" BASE_URL="$BASE_URL" bash scripts/tag_check.sh \
            || { echo "== gate FAILED: tag_check($CANDIDATE_TAG) — do NOT cut this tag"; exit 1; }
    fi
else
    echo "== clause 7: CANDIDATE_TAG unset — tag ritual not run (set CANDIDATE_TAG=vX.Y.Z when gating a release)"
fi
echo "== gate rc=$suite_rc (suite + all clause-6 positive controls green)"
exit $suite_rc

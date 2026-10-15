#!/usr/bin/env bash
# start_llama_servers.sh — start the two llama-server instances MiniCPM-V
# vision backends the app expects:
#   :8080 = 16x mmproj (fast, fewer visual tokens)   :8081 = 4x mmproj (detail)
#
# Configurable via env (defaults match the current pvellm deployment):
#   LLAMA_BIN      path to llama-server binary
#   MODEL_DIR      directory holding the GGUF files
#   GGPU           main-model filename (quant)
# Edit defaults below if your layout differs, or export before running.
set -euo pipefail

LLAMA_BIN="${LLAMA_BIN:-/usr/local/bin/llama-server}"
MODEL_DIR="${MODEL_DIR:-/AITOOLCHAIN/models/MiniCPM-V-4.7}"
GGUF="${GGUF:-MiniCPM-V-4_7-1B-Q4_K_M.gguf}"
MMPROJ_16X="${MMPROJ_16X:-mmproj-model-f16.gguf}"
MMPROJ_4X="${MMPROJ_4X:-mmproj-model-f16-4x.gguf}"
CTX="${CTX:-8192}"
LOGDIR="${LOGDIR:-$HOME/logs}"
mkdir -p "$LOGDIR"

for f in "$MODEL_DIR/$GGUF" "$MODEL_DIR/$MMPROJ_16X" "$MODEL_DIR/$MMPROJ_4X"; do
    [ -f "$f" ] || { echo "ERROR: missing $f — download models first (docs/DEPLOY.md step 2)" >&2; exit 1; }
done

start_one() { # port mmproj label
    local port="$1" mmproj="$2" label="$3"
    if curl -sf -m 2 "http://127.0.0.1:${port}/health" >/dev/null 2>&1; then
        echo "llama-server ($label) already up on :$port"
        return 0
    fi
    echo "Starting llama-server ($label) on :$port -> $LOGDIR/llama-$label.log"
    nohup "$LLAMA_BIN" -m "$MODEL_DIR/$GGUF" --mmproj "$MODEL_DIR/$mmproj" \
        --host 0.0.0.0 --port "$port" -c "$CTX" -ngl 99 \
        >"$LOGDIR/llama-$label.log" 2>&1 &
}

start_one 8080 "$MMPROJ_16X" 16x
start_one 8081 "$MMPROJ_4X" 4x

echo "Waiting for /health ..."
for port in 8080 8081; do
    for _ in $(seq 1 60); do
        curl -sf -m 2 "http://127.0.0.1:${port}/health" >/dev/null && { echo ":$port healthy"; break; }
        sleep 1
    done
done

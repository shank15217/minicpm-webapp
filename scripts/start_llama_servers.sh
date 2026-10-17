#!/usr/bin/env bash
# start_llama_servers.sh — start the two llama-server instances MiniCPM-V
# vision backends the app expects:
#   :8080 = mmproj-model-f16.gguf   (scale_factor=4 => 16x DOWNSAMPLE =>
#           64 tok/tile  => the FAST/general tower; measured 489/612 prompt
#           tokens vs 1851/2304 for -4x on the same image, 2026-10-17)
#   :8081 = mmproj-model-f16-4x.gguf (scale_factor=2 => 4x downsample =>
#           256 tok/tile => the DETAIL/OCR tower; ~4x prefill cost)
#   NOTE: the Oct-15 header here claimed ":8080 = more visual tokens
#   (837 vs 228)" — RETRACTED 2026-10-17, falsified by same-image re-measure
#   on two hosts plus GGUF header diff (user). Orientation pin:
#   tests/test_tower_orientation.py (MPW_TOWER_PROBE=1 runs it live).
#
# Configurable via env (defaults match the current pvellm deployment):
#   LLAMA_BIN      path to llama-server binary
#   MODEL_DIR      directory holding the GGUF files
#   GGPU           main-model filename (quant)
# Edit defaults below if your layout differs, or export before running.
set -euo pipefail

# Binary default is the pvellm/DEPLOY.md build location. The old default
# /usr/local/bin/llama-server exists on NO known host and nohup-failed
# silently into a log (user-visible symptom: "nothing starts", rc=0).
LLAMA_BIN="${LLAMA_BIN:-/AITOOLCHAIN/llama.cpp/build/bin/llama-server}"
MODEL_DIR="${MODEL_DIR:-/AITOOLCHAIN/models/MiniCPM-V-4.7}"
# Q8_0 per owner ruling (2026-10-15): best-quality quant of MiniCPM-V 4.7.
# Set GGUF=MiniCPM-V-4_7-1B-Q4_K_M.gguf to roll back.
GGUF="${GGUF:-MiniCPM-V-4_7-1B-Q8_0.gguf}"
# Tower naming note (corrected 2026-10-17): the suffix names the DOWNSAMPLE
# factor, not the token count. f16 = 16x downsample = 64 tok/tile = CHEAPER;
# "-4x" = 4x downsample = 256 tok/tile = MORE DETAIL. Variable names keep
# the historical 16X/4X labels; port truth: :8080 fast, :8081 detail.
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
FAILED=""
for port in 8080 8081; do
    label=$([ "$port" = 8080 ] && echo 16x || echo 4x)
    ok=""
    for _ in $(seq 1 60); do
        curl -sf -m 2 "http://127.0.0.1:${port}/health" >/dev/null && { echo ":$port healthy"; ok=1; break; }
        sleep 1
    done
    [ -n "$ok" ] || FAILED="$FAILED :$port"
done
if [ -n "$FAILED" ]; then
    # Fail LOUD: a starter that exits 0 while nothing came up hands the user
    # "nothing happened" instead of the truth (this exact script did tonight).
    echo "ERROR: llama-server never became healthy on:$FAILED" >&2
    for port in $FAILED; do
        label=$([ "$port" = ":8080" ] && echo 16x || echo 4x)
        echo "--- tail $LOGDIR/llama-$label.log ---" >&2
        tail -5 "$LOGDIR/llama-$label.log" >&2 2>/dev/null || echo "(no log file)" >&2
    done
    exit 1
fi

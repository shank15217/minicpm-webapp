#!/usr/bin/env bash
# tag_check.sh — mechanical release gate for a version tag (agreed in room 2026-10-14).
#
# Usage:  TAG=v0.2.0 ./scripts/tag_check.sh
# Requires a LIVE instance reachable at BASE_URL backed by a THROWAWAY baseline
# store (never the production one), e.g.:
#
#   export BASE_URL=http://127.0.0.1:8010
#   BASELINES_DIR=$(mktemp -d) PORT=8010 ./scripts/start_app.sh &
#   export DEAD_BACKEND_URL=http://127.0.0.1:8001   # an instance pointed at dead llama ports
#   TAG=v0.2.0 ./scripts/tag_check.sh
#
# Gate rules:
#   1. Full suite, all tiers, write probes ON:  WRITE_PROBES=1 pytest -rxX tests/
#      -> must exit 0 (xfail_strict=true makes any XPASS a failure).
#   2. The defects whose marks must be REMOVED by the tag must have no
#      xfail mark left in tests/ (their removal commit is the milestone signal).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
export PATH="$HOME/.local/bin:$PATH"

TAG="${TAG:?set TAG=vX.Y.Z}"
BASE_URL="${BASE_URL:?set BASE_URL to the throwaway instance}"

# Defect marks that MUST still be present (open) / absent (closed) per tag.
declare -A MUST_CLOSED=(
    [v0.2.0]="S6 S6b S7"
    [v0.3.0]="S1 S2"
    [v0.5.0]="S3 S4"
)
# Tags before the first fix release have no closure requirements yet.
CLOSE_LIST="${MUST_CLOSED[$TAG]:-}"

echo "== [1/2] full suite (all tiers, WRITE_PROBES=1, xfail_strict) against $BASE_URL"
WRITE_PROBES=1 uv run --extra dev pytest -rxX tests/ -v

echo "== [2/2] milestone xfail-mark removal check for $TAG"
for sid in $CLOSE_LIST; do
    # \b keeps S6 and S6b from matching each other.
    if grep -rnE "xfail\(.*DEFECT ${sid}\b" tests/ >/dev/null 2>&1; then
        echo "FAIL: $TAG requires the xfail mark for $sid to be REMOVED (defect closed)." >&2
        grep -rnE "DEFECT ${sid}\b" tests/ >&2
        exit 1
    fi
    echo "  $sid: mark removed ✔"
done

echo "== $TAG gate GREEN. Cut the tag with:"
echo "   git tag -a ${TAG} -m 'release ${TAG}' && git push origin ${TAG}"

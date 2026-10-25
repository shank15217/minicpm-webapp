#!/bin/sh
# Drift law (2026-10-25, Sammy): "committed" wearing "deployed" clothes is
# W11's quieter cousin — prose rules rot, timers that compare hashes don't.
# Two measured fixtures pin this script (tests/test_drift_check.py):
#   (1) manual unit refreshes (the %n era) left /etc copies diverging from
#       deploy/ copies unnoticed;
#   (2) 4803e14 was committed AND PUSHED while prod ran the pre-fix
#       page.sh — the pager debuted carrying the bug it shipped to fix.
# Fails loud -> OnFailure pages the (live) pager.
set -eu
REPO="${1:-/root/minicpm-webapp}"
ETC="${2:-/etc/systemd/system}"
fail=0
for f in "$REPO"/deploy/systemd/*.service "$REPO"/deploy/systemd/*.timer; do
    [ -e "$f" ] || continue
    b=$(basename "$f")
    c="$ETC/$b"
    [ -f "$c" ] || { echo "DRIFT: $b tracked in deploy/ but ABSENT from $ETC"; fail=1; continue; }
    cmp -s "$f" "$c" || { echo "DRIFT: $b differs from its $ETC copy"; fail=1; }
done
# committed-wearing-deployed: behind origin/main on RUNNING code pages
if git -C "$REPO" rev-parse --git-dir >/dev/null 2>&1; then
    git -C "$REPO" fetch -q origin 2>/dev/null || true
    behind=$(git -C "$REPO" rev-list --count "HEAD..origin/main" 2>/dev/null || echo 0)
    if [ "$behind" != "0" ]; then
        if git -C "$REPO" diff --name-only HEAD origin/main 2>/dev/null | grep -qE '^(deploy|scripts)/|\.py$'; then
            echo "DRIFT: prod $behind commit(s) behind origin/main and the delta touches running code (deploy/, scripts/, *.py)"
            fail=1
        else
            echo "note: prod $behind commit(s) behind origin/main (docs-only delta; deploy is gated, no page)"
        fi
    fi
fi
[ "$fail" = "0" ] && echo "drift-check: clean"
exit "$fail"

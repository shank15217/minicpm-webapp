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
# orphan-code law (2026-10-25, shadow ledger 2/2): untracked or modified
# *.py/*.sh in the working tree is "code older-or-orphaned-than-HEAD" —
# two live instances (paired_capture, judge.py on prod disk) both only
# harmless by byte-comparison luck. A shadow copy nobody can prove is
# identical must fail the timer, not wear luck as a status.
if git -C "$REPO" rev-parse --git-dir >/dev/null 2>&1; then
    shadow=$(git -C "$REPO" status --porcelain -- '*.py' '*.sh' || true)
    if [ -n "$shadow" ]; then
        echo "DRIFT: untracked/modified code files in $REPO (orphaned-or-modified vs HEAD):"
        echo "$shadow"
        fail=1
    fi
fi
[ "$fail" = "0" ] && echo "drift-check: clean"
exit "$fail"

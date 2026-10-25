#!/usr/bin/env bash
# W10: baseline truth backup. Run from cron/systemd-timer on the prod host.
# Usage: REMOTE=host:/path/backups ./backup_baselines.sh [baselines_dir]
#
# CONSISTENT SNAPSHOT (restore drill, 2026-10-25): runs.db is LIVE — the
# monitor writes hourly, and a hot tar of it can produce a torn DB that
# passes `tar tzf` and fails only at restore day. So: stage a copy where
# sqlite gets a real online-backup (python stdlib, no CLI dependency)
# and everything else a plain copy, then tar the staging dir. Restore
# drill asserts pragma integrity_check + row counts, not just tar listing.
set -euo pipefail
DIR="${1:-/root/minicpm-webapp/baselines}"
REMOTE="${REMOTE:?set REMOTE=user@host:/path/to/backup/target}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
cp -a "$DIR" "$STAGE/baselines"
python3 - "$DIR/runs.db" "$STAGE/baselines/runs.db" <<'PY'
import sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
s = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
d = sqlite3.connect(dst)
with d:
    s.backup(d)
d.close(); s.close()
PY
tar czf - -C "$STAGE" baselines | \
  ssh "${REMOTE%%:*}" "mkdir -p '${REMOTE#*:}' && cat > '${REMOTE#*:}/baselines-$STAMP.tgz.tmp' && mv '${REMOTE#*:}/baselines-$STAMP.tgz.tmp' '${REMOTE#*:}/baselines-$STAMP.tgz'"
# integrity spot-check: newest archive lists without error
ssh "${REMOTE%%:*}" "tar tzf '${REMOTE#*:}/baselines-$STAMP.tgz' >/dev/null"
# keep last 14 dailies
ssh "${REMOTE%%:*}" "cd '${REMOTE#*:}' && ls -1t baselines-*.tgz | tail -n +15 | xargs -r rm --"
echo "backed up $DIR -> $REMOTE/baselines-$STAMP.tgz"

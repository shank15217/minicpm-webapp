#!/usr/bin/env bash
# W10: baseline truth backup. Run from cron/systemd-timer on the prod host.
# Usage: REMOTE=host:/path/backups ./backup_baselines.sh [baselines_dir]
set -euo pipefail
DIR="${1:-/root/minicpm-webapp/baselines}"
REMOTE="${REMOTE:?set REMOTE=user@host:/path/to/backup/target}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
tar czf - -C "$(dirname "$DIR")" "$(basename "$DIR")" | \
  ssh "${REMOTE%%:*}" "mkdir -p '${REMOTE#*:}' && cat > '${REMOTE#*:}/baselines-$STAMP.tgz.tmp' && mv '${REMOTE#*:}/baselines-$STAMP.tgz.tmp' '${REMOTE#*:}/baselines-$STAMP.tgz'"
# integrity spot-check: newest archive lists without error
ssh "${REMOTE%%:*}" "tar tzf '${REMOTE#*:}/baselines-$STAMP.tgz' >/dev/null"
# keep last 14 dailies
ssh "${REMOTE%%:*}" "cd '${REMOTE#*:}' && ls -1t baselines-*.tgz | tail -n +15 | xargs -r rm --"
echo "backed up $DIR -> $REMOTE/baselines-$STAMP.tgz"

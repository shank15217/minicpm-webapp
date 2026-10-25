#!/bin/sh
# page.sh %i — pager body for mpw-page@.service (kept OUT of the unit to
# dodge systemd's ${} specifier reservation; a pager whose unit fails to
# PARSE is journald-only wearing a pager costume).
# Config: /etc/mpw-page.env (0600, operator-owned):
#   MPW_PAGE_URL  full POST endpoint. ntfy topic URL, or
#                 https://api.telegram.org/bot<TOKEN>/sendMessage for TG.
#                 The token lives ONLY in this file — never a unit
#                 argument; journald records ExecStart, and ExecStart is
#                 this script + %i, never the token-bearing URL.
#   MPW_PAGE_STYLE  "tg" -> body sent form-encoded as text= (Telegram's
#                 shape); anything else -> raw body (ntfy-native).
#   MPW_PAGE_TOKEN  optional bearer token (header only).
#   MPW_PAGE_COOLDOWN_S  per-unit cooldown window, default 3600.
#   MPW_PAGE_STATE_DIR   default /var/lib/mpw-page.
# Honesty rules in deploy/systemd/mpw-page@.service header.
#
# STORM LAW (Sammy, 2026-10-25): a pager that can storm is a self-muting
# pager — the CHDIR crash-loop (44 restarts/40 min) under this pager with
# no cooldown = 44 messages -> mute -> silence by OPERATOR hand, a red no
# journal will ever show. First failure pages in FULL; failures inside
# the window coalesce silently; the window's next failure pages ONCE
# carrying "N more failures since <first HH:MM> UTC". State surviving
# reboot is deliberate; reboot re-arms full detail (reboot IS a change).
set -eu
UNIT="${1:-unknown}"
# shellcheck disable=SC1091
[ -f /etc/mpw-page.env ] && . /etc/mpw-page.env
if [ -z "${MPW_PAGE_URL:-}" ]; then
    echo "mpw-page: PAGER NOT CONFIGURED for failure of $UNIT - set MPW_PAGE_URL in /etc/mpw-page.env"
    exit 1
fi
COOLDOWN="${MPW_PAGE_COOLDOWN_S:-3600}"
STATE_DIR="${MPW_PAGE_STATE_DIR:-/var/lib/mpw-page}"
mkdir -p "$STATE_DIR" 2>/dev/null || STATE_DIR="${TMPDIR:-/tmp}/mpw-page-state"
mkdir -p "$STATE_DIR"
SAFE=$(printf '%s' "$UNIT" | tr -c 'A-Za-z0-9._-' '_')
STAMP="$STATE_DIR/$SAFE.state"
NOW=$(date +%s)

post() {  # $1 = message body; URL/token come from env ONLY (never argv)
    if [ "${MPW_PAGE_STYLE:-}" = "tg" ]; then
        curl -fsS -m 15 --data-urlencode "text=$1" "$MPW_PAGE_URL"
    elif [ -n "${MPW_PAGE_TOKEN:-}" ]; then
        curl -fsS -m 15 -H "Title: minicpm-webapp alert" \
             -H "Authorization: Bearer $MPW_PAGE_TOKEN" \
             -d "$1" "$MPW_PAGE_URL"
    else
        curl -fsS -m 15 -H "Title: minicpm-webapp alert" \
             -d "$1" "$MPW_PAGE_URL"
    fi
}

LAST=$(sed -n 1p "$STAMP" 2>/dev/null || echo 0)
FIRST=$(sed -n 2p "$STAMP" 2>/dev/null || echo "$NOW")
SUPPRESSED=$(sed -n 3p "$STAMP" 2>/dev/null || echo 0)

if [ "$LAST" != "0" ] && [ $((NOW - LAST)) -lt "$COOLDOWN" ]; then
    # in-window: coalesce silently (local log line, no post)
    printf '%s\n%s\n%s\n' "$LAST" "$FIRST" "$((SUPPRESSED + 1))" > "$STAMP"
    echo "mpw-page: coalesced failure of $UNIT (suppressed=$((SUPPRESSED + 1)) within ${COOLDOWN}s)"
    exit 0
fi

FIRST_HHMM=$(date -u -d "@$FIRST" +%H:%M 2>/dev/null || date -u +%H:%M)
MSG="mpw ALERT: unit $UNIT failed on $(hostname) at $(date -u +%FT%TZ)"
if [ "${SUPPRESSED:-0}" -gt 0 ]; then
    MSG="$MSG — plus $SUPPRESSED more failures since $FIRST_HHMM UTC (per-unit cooldown active)"
else
    MSG="$MSG; see: journalctl -u $UNIT -n 50"
fi
post "$MSG"
printf '%s\n%s\n%s\n' "$NOW" "$NOW" 0 > "$STAMP"
echo "mpw-page: paged for $UNIT"

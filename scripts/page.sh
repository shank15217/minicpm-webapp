#!/bin/sh
# page.sh %i — pager body for mpw-page@.service (kept OUT of the unit to
# dodge systemd's ${} specifier reservation; a pager whose unit fails to
# PARSE is journald-only wearing a pager costume).
# Config: /etc/mpw-page.env -> MPW_PAGE_URL (POST endpoint: ntfy topic,
# TG sendMessage, any webhook), optional MPW_PAGE_TOKEN (bearer).
# Honesty rules in deploy/systemd/mpw-page@.service header.
set -eu
UNIT="${1:-unknown}"
# shellcheck disable=SC1091
[ -f /etc/mpw-page.env ] && . /etc/mpw-page.env
if [ -z "${MPW_PAGE_URL:-}" ]; then
    echo "mpw-page: PAGER NOT CONFIGURED for failure of $UNIT - set MPW_PAGE_URL in /etc/mpw-page.env"
    exit 1
fi
MSG="mpw ALERT: unit $UNIT failed on $(hostname) at $(date -u +%FT%TZ); see: journalctl -u $UNIT -n 50"
if [ -n "${MPW_PAGE_TOKEN:-}" ]; then
    curl -fsS -m 15 -H "Title: minicpm-webapp alert" \
         -H "Authorization: Bearer $MPW_PAGE_TOKEN" \
         -d "$MSG" "$MPW_PAGE_URL"
else
    curl -fsS -m 15 -H "Title: minicpm-webapp alert" \
         -d "$MSG" "$MPW_PAGE_URL"
fi
echo "mpw-page: paged for $UNIT"

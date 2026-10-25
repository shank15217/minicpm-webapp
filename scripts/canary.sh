#!/bin/sh
# Daily canary digest (Sammy 2026-10-25). Three jobs, ten lines:
# (a) the pager itself is unmonitored — a dead pager cannot page about
#     itself; this proves the alert path DELIVERED today;
# (b) day-validity token for the pager_day+30d clock: a counted day is a
#     day whose canary landed in the chat. Outage days never enter the
#     count of 30 — the skip-vs-reset argument never has to happen;
# (c) makes "certifies watching, not uptime" mechanically checkable.
# Runs as backup unit's ExecStartPost => fires only on backup SUCCESS.
set -eu
[ -f /etc/mpw-page.env ] && . /etc/mpw-page.env
[ -n "${MPW_PAGE_URL:-}" ] || { echo "canary: MPW_PAGE_URL unset - cannot prove watching"; exit 1; }
MSG="minicpm CANARY ok $(date -u +%FT%TZ): backup done, dailies=${1:-?}, newest-sha=${2:-?} — a day with this line in chat is a watched day"
if [ "${MPW_PAGE_STYLE:-}" = "tg" ]; then
    curl -fsS -m 15 --data-urlencode "chat_id=${MPW_PAGE_CHAT_ID:-}" \
         --data-urlencode "text=$MSG" "$MPW_PAGE_URL"
else
    curl -fsS -m 15 -d "$MSG" "$MPW_PAGE_URL"
fi
echo "canary: digest delivered"

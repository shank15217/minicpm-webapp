#!/bin/sh
# Daily canary digest (Sammy 2026-10-25). Three jobs:
# (a) the pager itself is unmonitored — a dead pager cannot page about
#     itself; this proves the alert path DELIVERED today;
# (b) day-validity token for the pager_day+30d clock: a counted day is a
#     day whose canary landed in the chat. Outage days never enter the
#     count of 30 — the skip-vs-reset argument never has to happen;
# (c) makes "certifies watching, not uptime" mechanically checkable.
# Runs as backup unit's ExecStartPost => fires only on backup SUCCESS.
#
# HONEST-FIELD LAW (Quinn 2026-10-25): the debut digest shipped
# newest-sha=e3b0c44298fc1c14 — the SHA-256 of EMPTY INPUT. Mechanism:
# $(ls ...) inside double quotes inside the unit's inline ssh argument
# expanded LOCALLY, where the archives don't exist; remote sha256sum
# with no file arg hashed empty stdin. v1 also would have accepted '?'
# or '' as a field. A watchdog field that proves nothing while looking
# alive is the week's pathology living inside the watchdog. So: hashing
# happens through the ssh channel where the bytes are, and a digest that
# would carry '', '?', or sha256('') REFUSES to deliver. Logic lives in
# THIS script, never inline in a unit line again.
set -eu
[ -f /etc/mpw-page.env ] && . /etc/mpw-page.env
[ -n "${MPW_PAGE_URL:-}" ] || { echo "canary: MPW_PAGE_URL unset - cannot prove watching"; exit 1; }
SSH_CMD="${MPW_BACKUP_SSH:-ssh mpw-gw}"
BDIR="${MPW_BACKUP_DIR:-/srv/mpwbackup}"
EMPTY_SHA16="e3b0c44298fc1c14"   # printf '' | sha256sum | cut -c1-16

COUNT=$($SSH_CMD "ls -1 '$BDIR'/baselines-*.tgz 2>/dev/null | wc -l" || echo "")
NEWEST=$($SSH_CMD "ls -1t '$BDIR'/baselines-*.tgz 2>/dev/null | head -1" || echo "")
SHA=$($SSH_CMD "sha256sum '$NEWEST' 2>/dev/null | cut -c1-16" || echo "")

# Refusals FIRST: a digest that looks alive but proves nothing is worse
# than no digest — it poisons the very day-token semantics it carries.
{ [ -n "$COUNT" ] && [ "$COUNT" != "0" ]; } || { echo "canary: no dailies visible at $BDIR - REFUSING to certify a watched day"; exit 1; }
case "$SHA" in
    ''|'?'|"$EMPTY_SHA16"*)
        echo "canary: newest-sha is empty/placeholder/sha256-of-empty ('$SHA') - refusing (Quinn fixture)"; exit 1;;
esac

MSG="minicpm CANARY ok $(date -u +%FT%TZ): backup done, dailies=$COUNT, newest-sha=$SHA — a day with this line in chat is a watched day"
if [ "${MPW_PAGE_STYLE:-}" = "tg" ]; then
    curl -fsS -m 15 --data-urlencode "chat_id=${MPW_PAGE_CHAT_ID:-}" \
         --data-urlencode "text=$MSG" "$MPW_PAGE_URL"
else
    curl -fsS -m 15 -d "$MSG" "$MPW_PAGE_URL"
fi
echo "canary: digest delivered"

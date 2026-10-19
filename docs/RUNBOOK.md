# RUNBOOK — minicpm-webapp v0.4.3 (first shippable)

Audience: @user. This is the hands-on test path for the product as it ships
today, plus the honest weaknesses ledger — everything here is measured, not
hedged. When you finish, your feedback items map to the numbered tests.

Ship state: tag **`v0.4.3`** (first release certified end-to-end by its own
gate: suite + positive controls + tag ritual in one command). `main` = tag +
docs + the starter-script fix (`4397321`), zero app-code delta (audited
twice, `git diff --stat v0.4.3..origin/main` = 5 files, all docs/scripts).
Two baselines are live on pvellm (`example.com`, `fhlb-of.com`).
Production state tonight: checkout sits at the tag but **nothing is serving
`:8000`** (the old pre-v0.4 process is gone); both towers are UP on Q8_0.

**Doc ownership (sync rule, per @user):** this file owns the TEST CRITERIA
(U1–U6) and the WEAKNESSES LEDGER (W1–W11). `docs/OPERATOR.md` owns the
COMMANDS. Where this file shows a command, OPERATOR.md is the source of
truth; if they ever disagree, OPERATOR.md wins and this file has a bug.

---

## 0. What this product is

A website health monitor that answers **whether** your page changed with a
deterministic pixel diff (no GPU, no model, exact on identical input), and
**what** changed with a 1B MiniCPM-V narrator that is only ever asked to
describe a flagged region — never to judge, score, or compare. The VLM
cannot flip a verdict; the diff owns it. Every check is logged to
`baselines/runs.db` with the per-band similarity vectors.

```
POST /analyze url=...            -> screenshot live page
   detdiff (deterministic, always): per-band dHash64+MAE vs baseline
   sim >= 0.995 on all bands      -> OK, no VLM call at all (zero cost, zero hallucination surface)
   band below threshold           -> VLM narrates ONLY the flagged crops (single-image, describe-only)
   all bands differ + height moved-> verdict "site-updated" -> human review, not a deface alert
```

## 1. Upgrade production pvellm `:8000`  (your hands — test "U1")

**Commands: see `docs/OPERATOR.md` Part A1, verbatim.** (Sync rule: the old
block that lived here named a systemd unit that does not exist on pvellm and
`git checkout v0.4.3`, which parks the broken starter script indefinitely —
two of tonight's three fix-doc-carried traps. They are deleted, not
annotated.) Order-of-operations doctrine stays: stop any listener → move
refs → start; the DB migration runs at app startup (tested contract:
`tests/test_startup_migration.py`), and skipping the stop hard-500s every
/analyze mid-window.

Towers: both UP tonight on Q8_0 via the fixed starter; the Q4-vs-Q8 choice
is one `GGUF=` env var at any future relaunch (`start_llama_servers.sh`
preserves anything already healthy).

**Verify round-trip (test U1):**
```bash
curl -s -X POST localhost:8000/analyze -F url=https://example.com/ | python3 -c \
 'import json,sys;d=json.load(sys.stdin);print(d["status"], d.get("verification_quality"), d["deterministic"]["page_sim"])'
```
Expect: `OK deterministic-band-sim ... 1.0`. A `backend_url=NULL` row in
run_history for an unchanged page is the zero-VLM path in writing.

## 2. Your tests (numbered — feedback maps to these)

**U1 upgrade round-trip** — above. Green = schema migration + det layer live.

**U2 catch a defacement you stage (static page).** Take any baselined site
that does NOT rotate (example.com). On a copy of the baseline PNG, paint a
big obvious change (black half, banner bar), serve the copy from a local
HTTP server, and point the monitor at it via a throwaway instance:
```bash
# serve your poisoned copy first (nothing listens on 18080 until you do):
mkdir -p /tmp/u2 && cp <your-poisoned-page.png> /tmp/u2/page.png
(cd /tmp/u2 && python3 -m http.server 18080 &)
BASELINES_DIR=$(mktemp -d) PORT=8013 ./scripts/start_app.sh &
# capture baseline from your served copy once, then swap the poisoned PNG in its place, then:
curl -s -X POST localhost:8013/analyze -F url=http://127.0.0.1:18080/page.png
```
(U1/U4-style one-liners assume the store where the site IS baselined —
against an empty throwaway store /analyze returns the clean JSON error
`No reference image available`, which is U6's non-baselined case.)
Expect: `deterministic.changed=true`, `changed_bands` names the band, and a
narration describing your banner. This works — measured: half-white page
0.259, band-blank 0.662, 1.3% banner 0.969 (page-level, static band).

**U3 the honest false-alarm test (dynamic page).** Run U2-style check against
`fhlb-of.com` three times 20 s apart. Expect: **flags fire on a perfectly
healthy rotating page** (measured band-0 sim 0.8281–0.9688 night over
night). This is not a bug to fix with the threshold — it is why the v0.5
tolerance profiles exist. Judge the product as: noisy on dynamic bands
until calibration lands; trust the flag, read the narration.

**U4 cost/behavior on unchanged pages.** Any baselined, unchanged site:
response should carry NO VLM narration and finish in ~3–6 s (screenshot +
pure-CPU diff). Confirm the run row (pvellm has no `sqlite3` CLI — use
python3):
```bash
python3 -c "import sqlite3; [print(r) for r in sqlite3.connect(
  'baselines/runs.db').execute('SELECT id, backend_url, det_similarity, det_band_sims \
FROM run_history ORDER BY id DESC LIMIT 3')]"
# (working-clone path shown; production equivalent:
#  /root/minicpm-webapp/baselines/runs.db)
```
Expect `NULL 1.0 [1.0...]` (backend_url NULL = zero VLM calls; band vectors
present per-run). Unchanged monitoring costs zero model time.

**U5 one-command gate.** On pvellm: `./scripts/qa_gate.sh` (~30 s). Expect
9 passed / 1 skipped / 5 xfailed, three positive controls, rc=0. Add
`CANDIDATE_TAG=v0.4.3` to watch the tag ritual run last (check-then-tag is
mechanical now).

**U6 break-it probes (please try these):** kill a tower (`fuser -k 8080/tcp`)
then run a check → expect `CHECK_FAILED LLAMA_UNREACHABLE`, never a silent
green. Block JS/hang a site (`url=https://httpbin.org/delay/30`) → expect
bounded failure, not a wedged app. Feed a non-baselined URL → expect
baseline-vs-live refusal or capture-only mode, as labeled in the response.

## 3. WEAKNESSES — published, with receipts

W1 **Sub-~1%-area edits sail through.** 1.3%-area banner → page_sim 0.969
vs threshold 0.995 on static bands. A one-line text swap is below the floor.
A monitor that says "verified" does NOT mean "bit-identical."

W2 **Dynamic bands are pixel-blind — measured.** fhlb hero rotation moves
band sim by up to 0.14; a 1.3% banner planted on it moves it 0.016. Clean
captures alert BELOW the static threshold while defaced ones pass: the
channel is noise-dominated in BOTH directions. Consequence TODAY (pre-
calibration): rotating sites = noisy flags; do not page anyone on a single
dynamic-band alert.

W3 **Overlay defacement passes BOTH v0.5.5 channels on a dynamic band.**
QA-verified: black bar + "CONGRATS - YOU HAVE BEEN HACKED" on the rotating
hero — pixel blind (W2), content anchor green (it checks facts are PRESENT;
overlay replaces nothing, and text_sample quotes surviving text). Disposition
ruled (Paul, owner): high-dynamic bands ship anchor+pixel **plus
masking-with-flag → human review**; a text-inventory channel (fraction of
expected strings present, own §2 mini-cal) is the real fix, on the v0.6
slate. Until then: on dynamic sites, defacement WITH content loss is caught;
defacement BY overlay is not automatically caught.

W4 **Threshold 0.995 is provisional.** Ordering/determinism proven; the
cut-point is what v0.5 §2 control runs (6h cron, accumulating now) exist to
calibrate per baseline/band. Expect behavior change at calibration.

W5 **Open defects pinned in the suite (the 5 xfailed):** S1 baseline
overwrite without confirm/auth, S2 SSRF (url param reaches internal hosts),
S2b redirect-SSRF, S4 structured score + verdict-derivation (v0.5 contract),
each xfail-marked and gate-visible — this is disclosure, not hidden debt.
Do not expose :8000 publicly before S2/S2b close; LAN-only per house policy.
  STATUS 2026-10-19: S2/S2b CLOSED (allowlist gate, per-hop httpx hook +
  Playwright route guard; pins in tests/test_ssrf_gate.py) with NAMED
  residuals: (a) resolved-IP enforcement (rebinding: wildcard name ->
  loopback/metadata) is httpx-side per-hop only; the Playwright route guard
  sees names, not resolved answers — Chromium-side rebinding is residual,
  mitigated there only by default-deny + IP-literal refusal; (b) TOCTOU:
  the gate resolves, then the client re-resolves; a TTL flip between the
  two is not pinned (connection pinning would close it; deferred);
  (c) globbed name -> another ALLOWED LAN host is indistinguishable from
  legitimate use — accepted for a LAN monitor.   SELF-LOCKOUT INCIDENT 2026-10-19 (Quinn): the first shipped default
  refused 5 of 8 stored prod baselines (fhlb x4 + amazon) — flagship-site
  monitoring was down a day, silently, because gate refusals write no
  run_history row. Closed three ways: default extended to cover every host
  in the prod index as of that date; /health.gate_lockout publishes any
  baseline the effective gate would refuse (the monitor now sees itself,
  W11 taste real); permanent pin test_shipped_default_covers_every_prod_
  baseline_host + watchdog test. Any future default change runs the gauntlet
  of that pin.
S1: MPW_ADMIN_TOKEN gates
  the only baseline-mutating path (capture_baseline; no delete endpoint
  exists as of this writing — any future mutation endpoint must share it).

W5b **v0.3 scope accepted: no-baseline failure-mode transition signal
(Sammy, 2026-10-18).** Targets that never graduated can never alert under
current doctrine — a host with no baseline can change how it fails
(e.g. persistent SSL_* -> DNS_FAILURE/NETWORK_UNREACHABLE, the console's
exact run_history biography) with zero output. Accepted as DEGRADED-level
(not finding-level), reading only run_history: alert when an error_code
run reaches N consecutive occurrences and is then replaced by a different
code sustained for N consecutive runs — the same N-consecutive primitive
router's unreachable-threshold needs, so both share one mechanism and one
noise dial. Explicit rejection: bare per-run code churn must never alert.
SIGNAL ALPHABET (Quinn amendment, same registry doctrine as the
attestation gate): sequencing reads HOST-STATE codes only — a closed
in-code set (DNS_FAILURE, NETWORK_UNREACHABLE, CONNECTION_REFUSED,
SSL_CERT_EXPIRED, SSL_CERT_UNTRUSTED, SSL_HOSTNAME_MISMATCH, CAPTURE_FAILED).
Client-intent codes (UNAUTHENTICATED_BASELINE_REJECTED and any future
guard/test-authored code) are FILTERED BEFORE sequencing: they record
operator probes, not host state — Quinn's 7 guard-rejection rows
interleaved in the console history are exactly the fake-transition source
this excludes.
Fixtures (measured, baselines/runs.db): POSITIVE = console 13 rows,
SSL_CERT_UNTRUSTED era (Oct 17-18) -> NETWORK_UNREACHABLE x2 (17:05);
NEGATIVE = the guard-interleaved stretch 06:15-06:42 — with the exclusion,
the sequencer must stay silent across it.

W6 **Narration is advisory prose.** The 1B narrator can misdescribe; it can
no longer flip a verdict (architecture + tests), but "what changed" text
should be read as a lead, not a finding. Verdict language in narration =
defect, file it.

W7 **Tall pages** (thousands of px) work band-by-band in detdiff, but the
narrator sees max 3 flagged crops (1280px each) worst-first; a change below
the 3rd-worst flagged band goes unnarrated that run (still flagged in
`changed_bands`). S8/tallpage e2e remains TALLPAGE-gated.

W8 **Site-updated routing means human-in-the-loop.** All-bands-differ + page
height moved = "site-updated", alert fires, narration skipped, and until you
re-baseline, subsequent checks keep comparing to the old page. Re-baseline
after legit redesigns.

W9 **Control/attestation is not complete.** The §2 profiles need 24h diurnal
coverage + the one-time human answer @qaquinn asked (is fhlb hero rotation
expected?) — until profiles graduate `diurnal_verified`, treat dynamic-band
thresholds as raw.

W10 **Single copy of the baseline, no backup, no second witness.**
`baselines/*.png` + `index.json` exist exactly once on pvellm, unversioned
and unbacked: disk loss resets every comparison, profile, and attestation to
zero, and a *silently modified* baseline (bad deploy, manual edit) makes a
defaced site compare green forever with nobody the wiser. Fix is cheap for
the risk — baseline dir under git or nightly `restic`/`rsync` to another box
plus a sha256 manifest checked at app start — but until it ships, treat
baseline integrity as an assumption, not a property. *(Sammy's first-night
objection, accepted into the ledger without argument.)*
  STATUS 2026-10-19: **manifest half SHIPPED (detection), backup half is
  operator cron.** Every new `capture_baseline` entry carries `sha256` of
  its PNG; `load_baseline` refuses a disagreeing file with named
  `BASELINE_INTEGRITY` (a silently edited baseline can no longer compare
  green), `baseline_integrity_report()` surfaces ok/corrupt/unmanifested in
  `/health`, and index + PNG writes are atomic (`tmp` + `os.replace`) with
  `INDEX_CORRUPT` refusing to read a half-written index as empty — closing
  review bug D's read-empty-then-wipe-everything path. Pre-manifest entries
  (the six shipped baselines) load as `unmanifested` until re-captured —
  hash is attached at capture, never after the fact, so a corrupt baseline
  can never launder itself by having a hash pinned to its damaged bytes.
  Residual: `scripts/backup_baselines.sh` exists (tar over ssh, verify,
  14-daily retention) but installing it into cron on pvellm is the
  operator's call; until installed, single-copy remains TRUE — detection,
  not durability.

W12 **Full-page capture missed lazy-loaded content (fixed 2026-10-19).**
The fhlb `page_sim 0.3809, 6/6 bands` alarm was NOT a site restructure and
not defacement: `full_page` screenshots do not scroll, so below-the-fold
`loading=lazy` sections never triggered and rendered as PURE WHITE in the
live shot (band y6219+ luma 255.0 vs baseline 61.8). Baselines captured on
a lucky-passing load vs checks on an unlucky one differ by ~0.5 page_sim
with the site visually identical — exactly the "looks the same to me"
report. Fix: pre-scroll the full height (600px steps, 120ms dwell), return
to top, wait best-effort for `img.complete`, then capture. Verified on
prod hardware (code copied to the box, prod app.py untouched, restored):
two consecutive fixed-regime captures score bands [~0.78, 0.995, 0.984,
0.984, 0.922, **1.0**] against the SAME baseline — the 0-white band is
gone; the only run-to-run movement left is band 0 (0.78 vs 0.80), which is
the genuinely rotating hero and belongs to W2/W3, not a capture defect.
Consequence: the "fhlb needs re-baseline (W8)" claim from the same day is
RETRACTED pending a post-fix re-check — the stored baseline is likely
still true; do not burn it on a defect. *(User's 'it looks visually the
same' beat three instruments' inference; capture-regime parity is now a
reviewed property.)*

W11 **v0.4.3 monitors nothing by itself.** No scheduler, no alert path, no
retention policy: "monitoring" means someone POSTs `/analyze` and reads JSON.
Until you add your own cron (or the v0.5 scheduler exists), **nothing is
being watched** — and `runs.db` rows accumulate per check with zero pruning,
which at a real 6h cadence is the slow failure mode (rows only — live
screenshots are *not* persisted per run; sprawl wording corrected per QA
audit). The "watched nothing for six days because nothing scheduled it"
class is a worse product failure than any detector gap on this list, which
is exactly why it's in the ledger and not in a footnote.
  **Status update (v0.5-dev kit pinned, NOT yet installed or on the ship
  tag):** closure kit exists — `monitorlib.py` + `tests/test_monitor.py`
  (`fa4a8ff`; 11 pins independently re-run green on a second, pvellm-blind
  host by QA): terminal-only heartbeat rows (a check that starts and dies is
  NOT fresh — ran ≠ completed), a `stale` deadline query with `--require-ok`
  (a watcher that runs but always fails reports `STALE BLIND`),
  `Persistent=true` systemd timers preferred over crontab (plain cron
  *forgets* missed windows across reboots — it does not queue), and an
  evidence-safe `prune`: dry-run by default, deletes **only** plain-OK
  non-control rows, so `CHANGED`/`SITE_UPDATED`/`CHECK_FAILED` rows cited by
  id in feedback lines and this ledger can never be pruned out from under a
  citation. W11 stays open until the units are installed (@user's hands) and
  the stale check itself has an owner — the W graduates when absence has a
  watchdog, not when the code exists.

## 4. Where to look at everything
```bash
# pvellm has no sqlite3 CLI — python3 reads the PROD store (absolute path;
# the U4 snippet's relative 'baselines/runs.db' assumes you cd into a repo
# clone first — same file layout, pick by which machine you're standing on):
python3 -c "import sqlite3; [print(r) for r in sqlite3.connect(
  '/root/minicpm-webapp/baselines/runs.db').execute(
  'SELECT id,ts,url,status,error_code,det_similarity,det_changed_bands \
FROM run_history ORDER BY id DESC LIMIT 10')]"
tail -5 /root/mpw-calibration/batch.log        # 6h control batch (cron, labeled, removable)
docs/calibration-protocol.md                   # the v0.5 rules the numbers above cite
```

## 5. Feedback format that maps back fastest
Per test U1–U6: PASS/FAIL + one line + (if FAIL) the command + the run_history
row id. Plus anything W1–W11 that bit you in real use — especially: did a flag
you saw look like noise (W2) or a real catch? That data IS the calibration.

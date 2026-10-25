# Roadmap — v0.5.1 → v1.0 (draft for room discussion, 2026-10-21)

## STATUS 2026-10-25 (supersedes nothing below; annotates it)

**Live baseline.** Prod pvellm `fdd0789` (socket truth), code truth
`03e6dca` (delta: gridwin capture lane + ceiling-lane parity + towers
heartbeat + tests; NOT deployed — Quinn's gate-first rule). Watcher
units installed on pvellm/pvellm2/pvellm3 (`Restart=always`, unit is
the launcher, hostname-form tower env in the unit, SSRF-safe).
Monitor + stale timers hourly `Persistent=`; stale check has fired
RED on real silence and gone green (journal-proven). Towers split
across two hosts; port truth pinned by unit case-map + probe.

**v0.6 items:** 1 MERGED (`origin/v0.5-dev` ancestor of main, verified);
2 DONE (watcher on prod — W11 dying-half closed; silence-half journal-
proven; PAGE ROUTING STILL OPEN: stale failures land in journald only,
no OnFailure= loud route, @user infra choice — W11 stays open-by-name
until routed); 3 NOT DONE = the doc/reconciliation pass, owed lines:
cadences stated from `crontab -l`; hostname-only tower rule ("IP
literals die at the gate by design"); t16 coords window-relative under
gridwin regime; `M uv.lock` + stray-db hygiene; OPERATOR Part A rewrite.
4 DONE (doc_lint wired as qa_gate clause 8 since the 2026-10-23 pass,
  verified in qa_gate.sh this session — an earlier annotation here said
  NOT DONE and was already stale when written; the doc-drift disease
  catching itself is the point of the clause).

**v0.7 items:** 1 ceilings-enable — GATED behind fit cohort
(`CURRENT_REGIME` bumped atomically with rig, fit single-shape guard
shipped `03e6dca`); 2 gridwin LANDED in code, activation = the gated
pull; 3 persistence clause: clause-9 numbers published (zero sustained
changes, 81h, controls fire), amazon pre-registered unscoreable; 4 quiet
ruling: implemented per row-level spec per gate owner.

**Clocks that start at the gated pull (not before):**
gridwin 24-fire ∧ ≥2-calendar-day ∧ distinct-window-hours graduation;
boundary-row quiet check (H₀=2560, lower-once to 2048); ceiling fit
(`min_days=5`, `min_rows`) cohort accrual under the new regime;
plant-order receipt chain (green→plant→RED→green) per HANDOFF-10.

**v1.0 distance, honestly:** the 30-day watched-with-zero-silent-green
clock started ~Oct 25 (watcher install day). SAMMY CORRECTION (Oct-25,
adopted): with journald-only alerting a red that pages nobody is
indistinguishable from an unaudited green, so a window elapsed BEFORE
the pager exists certifies uptime, not watching. **RC >= max(Nov 24,
pager_day + 30d)**, and the release definition now reads with its
silent-clause made operational: *a green is non-silent only if the same
pipeline that produced it would have produced a page had it been red.*
**PAGER IS LIVE (2026-10-25, `pager=tg`):** first deliveries 07:32:41Z
(acceptance test-fire) and 07:33:48Z (first real-unit page: an unplanned
backup failure that became its own proof); storm law verified both
branches within one hour (delivered + coalesced). **pager_day STAMPED
2026-10-25 by @qaquinn (five-item acceptance PASS at `4bef148`, her
hands).** Date arithmetic priced in (Sammy, adopted): both AND terms
land **2026-11-24** — a cliff, not a cushion, zero slack in max().
Day-validity semantics decided BEFORE week three: **a counted day is a
day whose CANARY digest landed in the chat** (daily success digest from
the backup unit's ExecStartPost); a pager-outage day never enters the
count of 30 — neither skip nor reset drama, and the unmonitored-pager
recursion (a dead pager cannot page about itself) is closed by the same
line.
**BACKUP IS LIVE + DRILLED (2026-10-25, `backup=gateway-disk`):**
nightly timer with day-one OnFailure page, online-sqlite snapshot,
restore drill sha256 8/8 + row-count 179/58 exact into throwaway dir;
seaweedfs explicitly left alone by the same decision ("lab, not a
service"). Fresh-machine DEPLOY verification and amazon capture
stabilization remain open; text-inventory channel (v0.8) has no code
yet. Everything that was gated on the two human words is now machine
work completed; what remains is clock-bound, QA-gated, or the named
defect work.

**Current weak points (ranked, updated 2026-10-25 post-words):** (1) CLOSED
— alerts reach a phone now (tg, storm-lawed); (2) amazon capture defect:
bounded for persistence/ceiling lanes by gridwin+guard, but the capture
itself still churns (growth+truncation) and the site is unscoreable
below its own epoch — **the one named engineering defect left**; (3)
ceiling layer trained on nothing until the new regime accrues — global
`DET_PASS_SIM` fallback is doing all real work; (4) CLOSED same day —
baselines durable on gateway-disk, drill executed (residual: failure
domain is host-loss, not site-loss, stated in the unit header); (5)
docs still carry pre-split topology folklore in DEPLOY.md until the
pass's second half lands (OPERATOR rewritten 2026-10-25).

Definition of 1.0 (proposed): **a site that baselines today is genuinely
watched tomorrow** — scheduled checks, per-block learned ceilings earned on
quiet data, an alert path with a watchdog, evidence durable across host loss,
and every doc claim reproducible by a stranger. Everything below maps to an
open W in the RUNBOOK ledger or a doc-lint finding (DOC-LINT-2026-10-21.md).

## DEFINITION OF DONE — USER RULING 2026-10-25 (authoritative; overrides above)

Verbatim intent: a small app that verifies whether **relatively static
sites** (fhlb-of.com class; app login/splash pages) are **(1) up, (2)
serving content, (3) serving content that isn't defacement**, using the
analytical + vision towers for a **status with reasonable confidence**.
Highly-dynamic sites are explicitly NOT the target ("doesn't need to
track highly dynamic sites like amazon").

Consequences, in order:
1. **amazon capture stabilization RETIRES** — the defect that owned this
   lane was a dynamic-site problem; out of scope by ruling. Baselines and
   history stay on disk (evidence is never deleted); amazon moves to a
   PAUSED roster: no monitor rows, no fit cohorts, docs say paused.
2. Remaining feature set to done: **(A) roster scoping** to the user's
   real static targets (only human input left: the URL list);
   **(B) defacement acceptance suite** — planted defaced fixture
   detected end-to-end within one monitor cycle, plus quiet-period
   false-positive measurement on the static roster: THIS is "reasonable
   confidence", quantified, not adjectives; **(C) one status surface**
   answering up/content/not-defaced per site with confidence + reason,
   BLIND stated loudly when blind (no silent green, unchanged law).
3. Unchanged because they ARE requirements (1)-(3)'s plumbing: monitor
   timers, towers health row, pager + storm law, canary day-tokens,
   drift check, gateway-disk backup. The ceiling layer (weak point 3)
   graduates from "nice" to core: it is the defacement detector.
4. Demoted, not deleted: persistence-corpus graduation and the gridwin
   clock were built for dynamic churn; on a static-only roster they keep
   accruing as bonus rigor but no longer gate the release. The Nov-24
   zero-silent-green watching clock STANDS (it certifies requirement-3
   alerting, which the pager+canary chain now makes checkable daily).

## v0.6 — "one line" (unblock everything else; low code, high decision value)

1. **Merge `origin/v0.5-dev` into `main`** (L7). Brings `monitorlib.py`
   (watcher + stale + evidence-safe prune), `scripts/calibrate.py`
   (graduation_blockers, trigger provenance), `anchorcheck.py`, monitor
   systemd units, cohort-zero + attestation-gate fixes. Conflict surface is
   the calibration subsystem — main's `calibration/{rig,fit}.py` are the new
   per-block doctrine; v0.5-dev's `scripts/calibrate.py` is the older
   per-band §2 tool. Keep BOTH as distinct stages (§2 profiles vs per-block
   ceilings) or fold, but say which — that's the one real merge decision.
2. **Install the watcher on prod** (W11 graduation): monitor + stale timers,
   stale check routed somewhere loud. This graduates the worst W on the
   board ("monitored nothing for six days").
3. **Doc reconciliation pass** (L8–L14): OPERATOR Part A rewritten against
   current prod, phantom `regime_filter.py` repointed to `fit.py`,
   DEPLOY release/security sections refreshed, cohort counts reconciled.
4. `scripts/doc_lint.sh` as qa_gate clause 8 (phantom paths / version skew /
   banned inverted-tower strings). Permanently kills the error class behind
   L1–L4.

## v0.7 — "calibration goes live" (the Oct-21+ queue already specced)

1. **Enable per-block ceilings in `app.py`** behind a flag
   (`CEILINGS_PATH`, default off): global `DET_PASS_SIM` stays the fallback
   for `status:"insufficient"` blocks; hero/rotator blocks fall back to
   global + narration (accepted doctrine). Gate: Quinn's acceptance fixture
   (planted movers + real careers/governance drama rows), per-block quiet
   spec, byte-identity assertions.
2. **Cadence bump + `capture_surface` row wiring** — LANDED 2026-10-21 on
   the steady-state audit PASS (Quinn, MINICPM-QA-REPORT-8): control cron is
   hourly on pvellm and runs the repo rig directly (the stale
   `/root/mpw-calibration/control_batch.py` copy is retired in place), so
   every row carries `capture_surface` AND a `mean_grid`; mean-grid corpus
   counting starts at the first hourly fire.
3. **Persistence clause graduates from spec**: ≥8 consecutive mean-grid
   rows, then first honest `persistence_events()` number on real cohort;
   sign-run detector + planted-63-mover fixture. Consumer guard shipped:
   `detdiff.persistence_corpus()` answers `"insufficient_corpus"` for
   corpus rows 1–7 — never a bare empty list. Honest state until then:
   schema-complete, trained on nothing.
4. **Per-block quiet final ruling** (Paul's starvation caveat vs Quinn's
   literal row-level spec) — implement whichever the gate owner signs.

## v0.8 — "the blind spot" (W3, the credibility feature)

1. **Text-inventory channel** with its own §2 mini-cal (fraction of expected
   strings present per band; VLM asks fact-questions only). Closes the
   overlay-defacement-on-dynamic-band hole that both existing channels walk
   through. Absent from 1.0 only if the mini-cal fails honesty gates — then
   the defacement-by-overlay floor stays published in the README, big letters.
2. **Baseline durability, second half** (W10): install
   `scripts/backup_baselines.sh` cron on prod + restore drill rehearsed once
   (detection shipped at v0.5.0; durability is an operator install + a drill).

## v0.9 — "multi-site, multi-operator"

1. Batch monitoring UX: `/analyze/batch` or scheduler-driven fleet over the
   whole baseline index; per-site cadence.
2. **Auth + rate limiting** (S1 token exists; add real authn before anything
   leaves the LAN — keeps the W5 boundary honest). Notification/alert routing
   config (currently operator-glued).
3. W5b failure-transition signal (N-consecutive code-change sequencer,
   host-state alphabet, Quinn's POSITIVE/NEGATIVE fixtures as pins).
4. Diff visualization (screenshot side-by-side + changed-block overlay) —
   cheap because the block grid already exists.

## v1.0 — release candidate requirements (all must be true)

- 8 sites × ≥30 days watched with zero silent-green incidents; stale-watcher
  watchdog has fired at least once in a drill (a watchdog that never barks
  is untested).
- Per-block ceilings enabled for every baseline with ≥48 quiet rows; global
  fallback documented for the rest; anti-ratchet rules pinned by tests.
- One restored-from-backup drill: baseline + runs.db + ceilings.json rebuilt
  on a second host, verdicts reproduced (W10 closed as *durability*).
- RUNBOOK re-verified top-to-bottom on a fresh machine via DEPLOY.md by
  someone who didn't write it; every U test passes.
- Doc-lint clause green in the release gate; README numbers regenerated
  from the live cohort, not remembered.

## Explicitly OUT of 1.0 (keep the perimeter honest)

35B-A3B model support (hardware-gated, not feature-gated) · login-wall/bot-
protected sites · public-internet exposure posture · auto-re-baselining
(the ceremony stays human + archiving by design).

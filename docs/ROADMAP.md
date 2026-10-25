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
4 NOT DONE (doc_lint as qa_gate clause 8).

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
Pager template pre-staged at deploy/systemd/mpw-page@.service (channel
= operator env choice; ntfy/email/TG all one POST). Backup/restore
drill (W10) and fresh-machine DEPLOY verification remain unstarted
operator tasks — and note (Sammy, Oct-25): `backup_baselines.sh` as
written CANNOT run from pvellm (zero outbound ssh auth edges, verified
twice this session); prerequisite is a destination decision + one
minted key, not a crontab line. Text-inventory channel (v0.8, the
blind-spot feature) has no code yet. Nothing blocking is unknown;
everything blocking is either gated-on-QA, clock-bound, or
operator-scheduled.

**Current weak points (ranked):** (1) page routing absent — a silence
now pages the journal, nobody's phone; (2) amazon capture defect:
bounded for persistence/ceiling lanes by gridwin+guard, but the capture
itself still churns (growth+truncation) and the site is unscoreable
below its own epoch; (3) ceiling layer trained on nothing until the new
regime accrues — global `DET_PASS_SIM` fallback is doing all real work;
(4) baselines durability = cron script installed? NO (v0.8.2 open) —
host loss today = re-baseline ceremony; (5) docs still carry pre-split
topology folklore until the doc pass lands.

Definition of 1.0 (proposed): **a site that baselines today is genuinely
watched tomorrow** — scheduled checks, per-block learned ceilings earned on
quiet data, an alert path with a watchdog, evidence durable across host loss,
and every doc claim reproducible by a stranger. Everything below maps to an
open W in the RUNBOOK ledger or a doc-lint finding (DOC-LINT-2026-10-21.md).

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

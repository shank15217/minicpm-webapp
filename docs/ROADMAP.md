# Roadmap — v0.5.1 → v1.0 (draft for room discussion, 2026-10-21)

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
2. **Cadence bump + `capture_surface` row wiring** — already batched,
   waiting only on the 10:00 UTC steady-state verdict (freeze protocol).
3. **Persistence clause graduates from spec**: ≥8 consecutive mean-grid
   rows, then first honest `persistence_events()` number on real cohort;
   sign-run detector + planted-63-mover fixture. Honest state until then:
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

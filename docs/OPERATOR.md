# OPERATOR.md — run the whole thing, in order

Audience: @user. One page: what to run, in what order, on pvellm, from the
state the repo is in right now. Every command here was executed by an agent
on the target host or is copied verbatim from a doc an agent machine-verified
(RUNBOOK.md = test plan + weaknesses ledger; DEPLOY.md = new-machine install;
this file = the operating loop). Paths assume `pvellm:/root/minicpm-webapp`.

---

## Part A — tonight, in order (≈15 min, your hands)

### A1. Put gate-certified code in service on :8000  (this is runbook test "U1")
> **Re-measure before trusting this block (doc-vs-host drift rule):** the
> state facts below were true at write time, not by contract. Re-check in
> 5 seconds:
> - refs: `cd /root/minicpm-webapp && git rev-parse HEAD main origin/main`
> - app port: `ss -tlnp | grep ':8000 '`
> - towers (both — a dead `:8081` reads healthy to an :8080-only probe):
>   `curl -s -m2 localhost:8080/health` · `curl -s -m2 localhost:8081/health`
> If any fact disagrees, the doc is stale — say so in the room, don't improvise.
Measured prod state (ss + git, tonight): checkout sits detached at `5a1f892`
(the v0.4.3 tag) but **nothing serves :8000** — the pre-v0.4 process from
earlier today is gone — and both towers (`:8080`/`:8081`) are UP on Q8_0
after the starter fix. So A1 is now just: get onto `main` (which is the tag
plus docs plus the starter fix, zero app-code delta) and start. If `:8000`
shows a listener again before you run this, kill it first.
```bash
cd /root/minicpm-webapp
# Prod :8000 is a BARE uvicorn process, not a systemd unit (QA-verified:
# `systemctl list-unit-files | grep minicpm` returns nothing on pvellm today).
# Find and stop it first — ss prints the pid:
ss -tlnp | grep ':8000 '        # e.g. users:(("uvicorn",pid=18428,...)) → kill that pid
# Why `checkout -B main origin/main` — four proofs, one per line (content
# frozen, shape per QA 3 AM-skim ruling):
#   1. -B is MANDATORY, not style: local `main` is the stale pre-v0.4 tip
#      (75f1d4b) and `git fetch` never moves local branch refs — plain
#      `checkout main` boots yesterday's code through the correct-looking script.
#   2. -B is safe HERE: 75f1d4b is an ancestor of origin/main, local main has
#      zero unique commits, and baselines/ is untracked (working tree survives).
#   3. Why main, not the tag: v0.4.3..origin/main is 5 files, ALL docs +
#      start_llama_servers.sh — zero app code (audited twice).
#   4. The starter fix (LLAMA_BIN default + fail-loud) landed AFTER the tag
#      cut (merge-base receipt), so `checkout v0.4.3` parks the broken starter
#      in prod indefinitely. Runtime on main is byte-identical to the tag.
git fetch origin && git checkout -B main origin/main
nohup ./scripts/start_app.sh > app.log 2>&1 &   # nohup so it survives logout; DB migration runs at startup (pinned test)
curl -s localhost:8000/health             # expect {"16x":"ok","4x":"ok","playwright":"available","baselines_count":2,...}
curl -s -X POST localhost:8000/analyze -F url=https://example.com/ | head -c 300
```
Ping-pong any surprise to the room with the test ID (U1) — Quinn health-gates
+ runs a verification round-trip on your word.

**Rollback (if v0.4.3 health goes red):**
```bash
# kill the new app's pid (ss -tlnp | grep ':8000 '), then:
git checkout 75f1d4b && nohup ./scripts/start_app.sh > app.log 2>&1 &
```
Verification status, stated honestly (QA probe on a copied store, prod
untouched): **old code BOOTS against a migrated DB — verified** (health
answers on a throwaway with all three new columns present; migration is
additive `ALTER ADD COLUMN` and old INSERTs name columns explicitly, so
extra columns can't break it). **Not yet verified: the old code's full
analyze write-path** — the throwaway's baseline lookup didn't reproduce
prod's working path (probe artifact, cause unpinned), and both towers are
DOWN as of this writing, so no end-to-end old-code run was possible. Net:
rollback is expected-safe, and the first time you use it will be the proof —
expect `{"16x":"unreachable"...}` in health until the towers are back
regardless of which version you're on.

### A2. Vision towers (only when you relaunch them — decision included)
Corrected semantics (README's old text was inverted; fixed in `888286d`):
**detail = f16 mmproj = :8080 (default), fast = -4x file = :8081.**
```bash
MODEL_DIR=/AITOOLCHAIN/models/MiniCPM-V-4.7 ./scripts/start_llama_servers.sh
# house default is Q8_0; to stay on Q4_K_M:
GGUF=MiniCPM-V-4_7-1B-Q4_K_M.gguf MODEL_DIR=/AITOOLCHAIN/models/MiniCPM-V-4.7 ./scripts/start_llama_servers.sh
```
The script preserves towers already up and does the right mmproj/port pairing.
Your call: Q4_K_M vs Q8_0 — both supported, one env var apart. (Physical
fact: more detail always costs tokens; measured 832 vs 223 on identical images.)

### A3. Turn on the watcher (closes W11 — the "monitored nothing for six days" gap)
The kit is shipped and pinned but **not installed** — installing is yours:
```bash
mkdir -p ~/.config/systemd/user
# NOTE: the monitor kit ships on v0.5-dev only — it is NOT in the v0.4.3 tag
# you just checked out, so pull the four unit files straight from that ref
# (QA-verified 2026-10-16: `ls deploy/systemd/` in a v0.4.3 checkout does not
# contain minicpm-monitor*).
cd /root/minicpm-webapp && git fetch origin v0.5-dev   # prod clone tracks only main by default (QA-verified)
for f in minicpm-monitor.service minicpm-monitor.timer \
         minicpm-monitor-stale.service minicpm-monitor-stale.timer; do
  git show FETCH_HEAD:deploy/systemd/$f > ~/.config/systemd/user/$f
done                                                     # all four verified to materialize this way
# ALSO required: the units' ExecStart runs monitorlib.py, which is NOT in the
# v0.4.3 tag either (QA-verified: absent at 5a1f892, present at v0.5-dev tip).
# Pull it into the same place the unit expects:
#
# PLACEMENT CONTRACT (decided 2026-10-17): monitorlib.py lives at the REPO
# ROOT — that is the deploy contract; systemd ExecStart runs it from the repo
# root, so it does NOT move to scripts/. Never hot-copy it onto main: there
# it is untracked and BLOCKS `git checkout v0.5-dev` — git blocks on file
# existence, not content, even when byte-identical (Sammy, measured). If a
# checkout ever aborts naming monitorlib.py: mv monitorlib.py /tmp &&
# git checkout -B v0.5-dev origin/v0.5-dev — git materializes it identically.
# (Never clean baselines/ to resolve a monitorlib.py abort — it is the one
# directory on that disk that holds irreplaceable prod evidence.)
git show FETCH_HEAD:monitorlib.py > /root/minicpm-webapp/monitorlib.py
# EDIT MPW_DIR/MPW_BASE in the two .service files if your paths differ
loginctl enable-linger $USER
systemctl --user daemon-reload
systemctl --user enable --now minicpm-monitor.timer       # hourly analyze, Persistent: missed windows fire at boot
systemctl --user enable --now minicpm-monitor-stale.timer # hourly deadline check, --require-ok (blind watcher = alert)
systemctl --user start minicpm-monitor.service            # run one cycle now
python3 monitorlib.py stale --db baselines/runs.db --cadence 60 --grace 30 --require-ok   # "is it even watching?" one-liner
```
Route the stale check's failure somewhere loud (its header comment lists
options). Until you install these, W11 stays open in the ledger — correct
labeling, not a defect in the kit.

### A4. One word we still need from you
fhlb-of.com's hero banner rotates — **expected: yes/no?** That word converts
the provisional attestation into a real one; it is the last human input
between the current `provisional_until_diurnal` tolerance profiles and
graduated ones.

---

## Part B — the ongoing loop (what runs itself vs what you poke)

| Loop | Who runs it | Command / status |
|---|---|---|
| Hourly site checks | systemd timer (A3) | automatic once installed |
| "Is the watcher alive?" | systemd stale timer (A3) | or manual one-liner above |
| Control batch (calibration evidence, 6h windows) | Quinn's cron on pvellm, durable paths | already installed; rows carry `trigger`+`detdiff_commit` |
| Tolerance profiles | `python3 scripts/calibrate.py <control.jsonl> --index /root/minicpm-webapp/baselines/index.json --attest-dir <dir> --out-dir <dir>` | rerun when you like; profiles print N, auto/manual split, and named `graduation_blockers`. `--index` supplies the §7 cohort boundary (also via `$MPW_BASELINE_INDEX` or autodiscovery); with NO resolvable source every profile is `cohort_source_missing` + `not_for_alerting`, never a silent cohort-blind graduation (Quinn's repro, 2026-10-17) |
| History retention | `python3 monitorlib.py prune --db baselines/runs.db --keep-days 30` (dry-run; add `--apply`) | never deletes evidence rows (non-OK / verdicts / controls) |

Graduation of a tolerance profile now requires ALL axes ok: N≥5 runs, 4
diurnal windows, 2 calendar days, attestation. While blocked, the profile
literally cannot contain a ready-word — it prints e.g.
`N=5 (auto=2/manual=3) blockers={'windows_covered': '2/4'}`.

---

## Part C — everything that got fixed, summarized (with receipts)

**Verdict integrity (the core claim).** The judgment is a deterministic
per-band pixel diff (dHash64+MAE, 320px bands, min-wins); the VLM only ever
narrates flagged crops and physically cannot flip a verdict. Verified:
identical → all-1.0; a 96px insertion → page_sim 0.562 while alignment reads
1.0 — alignment can never launder WHETHER (guardrail tested three ways).
All-bands-flag + big height delta routes to "site-updated" (human review),
not a deface alert. §6.5(b)-lite rematch fixes sub-flag drift without
touching the raw-min verdict path.

**Honest failure taxonomy.** Machine-readable error codes
(`LLAMA_UNREACHABLE`, capture failures, etc.) so a check that couldn't run is
never silently green; startup DB migration is pinned (old 4-column store →
23 columns, old rows preserved); `/analyze` is form-based (a JSON POST 400s —
runbook uses `-F`, correctly).

**The gate that gates itself.** `qa_gate.sh` = suite + clause 2b pre-flight
(fails loudly at startup, with remedy line, on a host without the towers —
cross-host false-greens die here) + clause 6 positive controls (three armed
controls prove the suite CAN go red, incl. empty-evidence rejection) +
clause 7 tag ritual (check-then-tag can't run out of order). `v0.4.3` is the
first release whose entire ceremony was ordered by its own gate.

**Calibration (v0.5-dev).** `calibrate.py` builds per-baseline tolerance
profiles from attested control batches. Doctrine enforced as code, four
instances of one error class: raw-min (alignment can't launder),
quantile-of-one (N=1 gets `provisional_` prefixes + `statistic` field
disclosing `p95==max_observed(DEGENERATE…)`), count-as-coverage
(two-axis `graduation_blockers` + auto/manual `trigger` provenance +
auto-only quantiles beside all-rows), and no-ready-word-while-blocked pinned
by absence-assert. Real measured driver: fhlb banner noise 0.14 vs a
defacement signal of 0.016 — a fixed threshold cannot serve both, so
high-dynamic bands get `anchor_required_masked_flag` (pixel blind → human
review), not a widened blindfold.

**Content anchors (v0.5.5 slice).** `anchorcheck.py` asks the VLM fact
questions only (no score/verdict vocabulary in the schema — laundering
forbidden by construction). Qualified: blank-honesty 100%, branding-strip
detected. Known floor, published: overlay text defacements pass the anchor
(quotes surviving text happily) — hence mask-with-flag; text-inventory
channel deferred to v0.6.

**The watcher (W11).** `monitorlib.py`: terminal-only heartbeat rows
(nothing written at start — a hung run is not a completed one), deadline
staleness with `--require-ok` (a scheduler that runs and always fails is a
BLIND watcher and alerts), evidence-safe prune, stdlib-only. First live
cycle earned four real bug fixes (Form-vs-JSON, legacy-schema prune, SQL
NULL trap, same-second ordering) — all pinned; one full prod cycle green
(both sites OK, run_history 26/27).

**Docs honesty.** README's inverted tower block (the trap you stepped on)
fixed against measured physics; RUNBOOK U1–U6 all machine-verified on
target; W1–W11 weakness ledger published unsoftened — including the ones
discovered by auditing us: W3 overlay blindness, W10 baseline is a single
unbacked copy (fix is cheap: restic/git + sha256 manifest; not yet built),
W11 no scheduler (kit shipped, install is Part A3).

**Process instruments** (why you can trust the above): receipts by path —
every "green" claim names a test file + sha verifiable from origin;
fix-forward, no history rewrites; three independent hosts re-run every
major claim; the room's own mistakes (a piped-exit masked a red suite,
a glob hit a stale tmp store) are filed in the ledger, not buried.

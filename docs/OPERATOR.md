# OPERATOR.md — run the whole thing, in order

Audience: @user. One page: what to run, in what order, on pvellm, from the
state the repo is in right now. Every command here was executed by an agent
on the target host or is copied verbatim from a doc an agent machine-verified
(RUNBOOK.md = test plan + weaknesses ledger; DEPLOY.md = new-machine install;
this file = the operating loop). Paths assume `pvellm:/root/minicpm-webapp`.

---

## Part A — the operating loop, UNIT AS LAUNCHER (measured 2026-10-25)

**Topology (all measured, this file states cadences from `crontab -l`, never
memory):** app `minicpm-webapp.service` on pvellm (`:8000`, unit-owned env);
towers SPLIT across hosts — `minicpm-llama@8080` (f16/fast) on **pvellm2**,
`minicpm-llama@8081` (-4x/detail) on **pvellm3**; `minicpm-monitor.timer` +
`minicpm-monitor-stale.timer` hourly, `Persistent=true`; control rig
`0 * * * *` and viability `5 * * * *` (both hourly, cron on pvellm, durable
paths). Port→mmproj pairing is PINNED in the tower unit's case-map, and the
crossed-pair check is a committed probe:
`env -u PYTHONPATH .venv/bin/python scripts/tower_orientation_probe.py`
(fast must pay FEWER prompt tokens than detail; crossed = exit 1).

**Ten-second health check (all four layers):**
```bash
curl -s -m5 localhost:8000/health | head -c 300   # commit, four "ok", gate_lockout []
systemctl is-active minicpm-webapp minicpm-monitor.timer minicpm-monitor-stale.timer
curl -s -m4 http://pvellm2.nellienet.win:8080/health; curl -s -m4 http://pvellm3.nellienet.win:8081/health
python3 /root/minicpm-webapp/monitorlib.py stale --db baselines/runs.db --cadence 60 --grace 30 --require-ok
```
Nine heartbeat rows expected (8 sites + the unconditional `towers` row).

**Restart / deploy:** everything through systemd —
`systemctl restart minicpm-webapp`; tower units restart on their own hosts.
Code moves: `git fetch` FIRST, then `git pull --ff-only` (installs-from-git
fetch this turn, every time — a tracking ref is not a fetch; a stale ref +
unit `cp` shipped the condemned unit through 44 crash-loops, scar receipt
2026-10-25), refresh `/etc/systemd/system/` copies + `daemon-reload` if unit
files changed. `scripts/start_app.sh` is **dev-only**; `nohup` launches are
the W11 defect class and are not the prod path anymore.

**Tower wiring rules (each learned as an outage):** app finds towers ONLY
via `LLAMA_SERVER_FAST` / `LLAMA_SERVER_DETAIL` in the UNIT file (shell
exports rot; legacy `_16X/_4X` aliases still shadow — keep them absent).
**Hostname form only: IP literals die at the S2 SSRF gate by design**
(`TARGET_NOT_ALLOWED`); that refusal is the self-lockout path working —
widen `MPW_ALLOW_TARGETS` never, especially not at 2 AM. Acceptance after
any tower change: probe uncrossed + `/health` four `ok` + `towers` row OK.

**Pager (LIVE since 2026-10-25T07:32Z, `pager=tg` by @user):** stale/backup
failure fires `OnFailure=mpw-page@%N.service` → form-encoded TG into the
operator's DM (chat id in `/etc/mpw-page.env`, 0600, token lives nowhere
else; **TG quirk measured: POST query strings are ignored — chat_id rides
in the body**, pinned in code). Storm law live-verified both branches the
first hour: 2 delivered pages, 1 coalesced duplicate. First delivery =
**pager_day candidate 2026-10-25** (Quinn stamps the 30-day clock after her
acceptance pass). Field lesson banked the hard way: destination truth comes
from `ip -4 -o addr` on the target itself — the gateway is **.209**, a
stale `.22` in the first unit draft failed against a stranger host with
ZERO log lines on our side.

**Baselines backup (LIVE since 2026-10-25, `backup=gateway-disk` by
@user; seaweedfs left alone by the same decision):** nightly 03:30Z
`minicpm-backup.timer` (Persistent), pushes tar.gz to gateway
`mpwbackup@192.168.35.209:/srv/mpwbackup` (dedicated keypair,
`from=…,restrict` authorized_keys — pvellm's one purpose-scoped outbound
edge), 14 dailies, `IOSchedulingClass=idle`, **OnFailure page from day
one**. Runs.db is snapshotted via python-stdlib sqlite online-backup
into staging before tar (hot-tar tears DBs silently; drill finding).
Restore drill executed same day: sha256 8/8 match, row counts 179/58
exact, integrity_check ok, into throwaway dir only. Failure domain:
survives loss of pvellm, not of the LAN/site.

**Baseline re-capture: still SUSPENDED** (2026-10-19 capture-defect trio +
amazon churn open): re-check order boot receipt → fhlb/amazon check rows →
only then any re-capture decision. Cohort admission is strict regime
equality to `fit.CURRENT_REGIME` (now `prescroll-4+gridwin2560`, atomic
with the rig constant, pinned by test); gridwin-era producer claims cite
digest `9219a9da8705`, not the pre-gridwin `a6ecce33a3ed`.


---

## Historical appendix — pre-unit era (SUPERSEDED; preserved as receipts)
Bare-process rituals, user-unit monitor install kit, and the canonical
restart ritual below describe the pre-2026-10-25 world (nohup app, local
towers, ~/.config/systemd/user timers). Kept because they are the scar
record; do not execute them on the unit-era host.

## Canonical restart ritual (docs-only; authored by @qaquinn from the room spec after two scanner-blocked attempts by its original author)

Restarting after any deploy hop MUST move refs before touching the process, so `HEAD`
never lags disk (tonight's recurring HEAD-lie — disk hot-materialized, HEAD stale, twice):

```bash
cd /root/minicpm-webapp
git fetch origin && git checkout -B main --force origin/main   # refs first
git rev-parse HEAD origin/main                                  # must print the SAME sha
kill $(pgrep -f "uvicorn app:app")
nohup ./scripts/start_app.sh > app.log 2>&1 &
curl -s localhost:8000/health | head -c 120                     # feature-keys confirm the NEW process
```

Verify the quoted sha is the feature commit you expect before believing any feature claim.
If `checkout` aborts on `monitorlib.py`, see W11: move it to `/tmp` and retry — never clean `baselines/`.

## Part B — the ongoing loop (what runs itself vs what you poke)

| Loop | Who runs it | Command / status |
|---|---|---|
| Hourly site checks | systemd timer (Part A) | INSTALLED 2026-10-25 (Persistent catches missed windows) |
| "Is the watcher alive?" | systemd stale timer (Part A) | hourly; pages via mpw-page@ once a channel exists |
| Control batch (calibration evidence, HOURLY — crontab `0 * * * *`, was 6h pre-Oct-21) | rig on pvellm, durable paths | rows carry `trigger`+`capture_regime` (gridwin era: `prescroll-4+gridwin2560`) |
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

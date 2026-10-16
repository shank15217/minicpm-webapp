# Deploying minicpm-webapp on a new machine

End-to-end guide: from a bare Linux box to a running synthetic website-health
monitor (Playwright screenshots judged against a MiniCPM-V baseline via llama.cpp).

Two supported distro families: **RHEL-family** (Rocky/Alma/RHEL/Fedora — verified on
Rocky Linux 10.2) and **Debian-family** (Debian/Ubuntu). Everything Python goes
through **uv**; never use `python -m pip` (uv venvs contain no pip — verified on pvellm).

## Architecture recap

```
client ──▶ FastAPI app (port 8000) ──▶ llama-server :8080 (detail: mmproj-model-f16,
                 │                            full-res, ~837 visual tokens / 640x480)
                 │                 └──▶ llama-server :8081 (fast: mmproj-model-f16-4x,
                 │                            4x downsample, ~228 tokens — cheaper, coarser)
                 └─▶ detdiff (deterministic band diff: owns the verdict, no GPU)
                 └─▶ Playwright Chromium (headless, full-page screenshots)
                 └─▶ baselines/  (PNG + index.json; gitignored runtime data)
```

## Step 0 — Prerequisites

- Linux x86_64 or aarch64, 2 GB+ free RAM for the app (+ GPU/RAM for llama.cpp below)
- Root or sudo (system packages). The app itself can run unprivileged.
- Outbound HTTPS for model + browser downloads.

## Step 1 — One-shot project install

```bash
git clone https://github.com/shank15217/minicpm-webapp.git
cd minicpm-webapp
sudo ./scripts/install.sh
```

What `install.sh` does (idempotent):

| Concern | RHEL-family (dnf) | Debian-family (apt) |
|---|---|---|
| Base tools | `curl ca-certificates` | same |
| Chromium runtime libs | `nss nspr atk at-spi2-atk at-spi2-core cups-libs libdrm libxkbcommon libX11 libxcb libXext libXfixes libXcomposite libXdamage libXrandr mesa-libgbm alsa-lib pango cairo` | `libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 libasound2 libpango-1.0-0 libcairo2` (auto-`t64` fallback on Debian 13 / Ubuntu 24.04) |
| uv | `curl -LsSf https://astral.sh/uv/install.sh \| sh` → `~/.local/bin/uv` | same |
| Python | uv manages CPython ≥3.10 (no distro python package needed) | same |
| Venv | `uv sync --extra dev` (uses `uv.lock`) | same |
| Browser | `uv run playwright install chromium` (libs from dnf) | `uv run playwright install --with-deps chromium` |
| Smoke test | launches headless Chromium, prints version | same |

> **RHEL note (the pitfall we actually hit):** Playwright's `--with-deps` is
> apt-only. On RHEL-family you must install the Chromium runtime library set
> above via dnf *before* `playwright install chromium`, or Chromium fails with
> missing-`libgbm`/`libnss3` errors at launch. `install.sh` handles both paths.

Two more field-verified notes (virgin Rocky 10.2 VM rehearsal, QA-REPORT-3,
SELinux Enforcing, install passed first try):

- **"OS version not officially supported" fallback banner on RHEL is EXPECTED.**
  Playwright publishes official Linux targets for Ubuntu/Debian only; on RHEL
  it downloads the `ubuntu24.04` build and it works (Chromium 156 launched
  headless under Enforcing SELinux with no policy changes). Don't chase it.
- **Browser installs are `$HOME`-scoped.** `install.sh` run as root puts
  Chromium in `/root/.cache/ms-playwright`. If the app later runs as a
  different deploy user, re-run `uv run playwright install chromium` as that
  user (or pre-seed its `$HOME/.cache/ms-playwright`), or it will fail to
  find the browser at launch.

## Step 2 — llama.cpp + MiniCPM-V model

The app is a client of two `llama-server` processes; they are NOT installed by
`install.sh`.

1. Build llama.cpp with MiniCPM-V 4.7 support (PR #29416 or later):

   ```bash
   git clone https://github.com/ggml-org/llama.cpp && cd llama.cpp
   cmake -B build && cmake --build build --config Release -j
   sudo cmake --install build        # provides /usr/local/bin/llama-server
   ```

   GPU build flags (ROCm/CUDA) per your hardware; CPU-only works — the 1B Q4
   model is small.

2. Download model + both vision towers (~1–2 GB total):

   ```bash
   pipx download huggingface-hub 2>/dev/null || uv tool install "huggingface_hub[cli]"
   hf download openbmb/MiniCPM-V-4.7-1B-gguf MiniCPM-V-4_7-1B-Q8_0.gguf  --local-dir /opt/models/MiniCPM-V-4.7
   hf download openbmb/MiniCPM-V-4.7-1B-gguf mmproj-model-f16.gguf        --local-dir /opt/models/MiniCPM-V-4.7
   hf download openbmb/MiniCPM-V-4.7-1B-gguf mmproj-model-f16-4x.gguf     --local-dir /opt/models/MiniCPM-V-4.7
   ```

   **Q8_0 is the owner-selected default** (best quality at this size; CPU/GPU
   footprint is still tiny for a 1B model). To roll back to the smaller
   quant: `GGUF=MiniCPM-V-4_7-1B-Q4_K_M.gguf ./scripts/start_llama_servers.sh`.

   > **Tower naming trap (cost us a wrong-labelled startup script):** `4x` in
   > this filename means **4x downsample**, i.e. the *coarser* tower.
   > `mmproj-model-f16.gguf` (no suffix) is the **full-resolution / detail**
   > tower. Measured on the same 640x480 image: `:8080` (f16) = 837 prompt
   > tokens, `:8081` (f16-4x) = 228 — ratio ≈ 3.7 ≈ the 4x downscale. If small
   > text goes undetected, check you are reading the *detail* port.
   >
   > App-facing names are semantic: `mode=detail` → f16, `mode=fast` → f16-4x.
   > `16x`/`4x` are accepted as deprecated aliases and are mapped in
   > `app.py:MODE_ALIASES`.

## Step 3 — Start services

Named scripts (each is idempotent; they health-check before starting):

```bash
MODEL_DIR=/opt/models/MiniCPM-V-4.7 ./scripts/start_llama_servers.sh   # :8080 (16x) + :8081 (4x)
./scripts/start_app.sh                                                 # app on :8000
```

App env overrides: `PORT`, `HOST`, `BASELINES_DIR`,
`LLAMA_SERVER_16X` (detail/f16 backend URL), `LLAMA_SERVER_4X` (fast backend
URL), `DET_PASS_SIM` (deterministic change threshold, default `0.995` —
provisional until the v0.5 control calibration; see
`docs/calibration-protocol.md`).

## Step 4 — systemd (optional, for permanent monitors)

Unit files in `deploy/systemd/` (`minicpm-llama@.service`, `minicpm-webapp.service`).
Enable with:

```bash
sudo cp deploy/systemd/*.service /etc/systemd/system/
sudo systemctl enable --now minicpm-llama@8080 minicpm-llama@8081 minicpm-webapp
```

Adjust `User=`, `WorkingDirectory=`, and `Environment=` lines to your paths first.

## Step 5 — Verify + first baseline

```bash
curl -s localhost:8000/health
# Capture a known-good baseline:
curl -s -X POST localhost:8000/analyze -F 'url=https://your-site.example/' \
     -F 'capture_baseline=true' -F 'file=@probe.png;type=image/png' | head -c 300
```

## Upgrading an existing install (schema migration order matters)

**Supported order is stop → upgrade → start.** Do not sync new code under a
*live* old app and leave it running.

`run_history` gains columns (`kind`, `det_similarity`, `det_changed_bands`,
`det_band_sims`, `det_aligned_band_sims`, ...) via `ALTER TABLE` inside
`init_run_db()`, which runs **once at app startup**. `CREATE TABLE IF NOT
EXISTS` never adds columns to a table that already exists. So an old-schema
`runs.db` is only migrated when a new-code process starts. Upgrading files
under a live old app hard-500s every `/analyze` in the window (field-observed,
QA-REPORT-6), and the reverse mismatch — new code writing columns an old app
never reads — silently strands history rows.

```bash
# 1. STOP first.
sudo systemctl stop minicpm-webapp            # or kill the start_app.sh process

# 2. Upgrade the tree to the release you want (tag, or main after its FF).
cd /path/to/minicpm-webapp && git fetch origin && git checkout v0.4.2
uv sync --extra dev

# 3. START. init_run_db() runs here; the ALTERs land on the existing runs.db.
sudo systemctl start minicpm-webapp            # or ./scripts/start_app.sh

# 4. Health-gate + one real round-trip before calling it upgraded.
curl -sf localhost:8000/health
curl -s -X POST localhost:8000/analyze -F 'url=https://your-baseline-site/' \
     -F 'mode=detail' | python -c 'import json,sys; d=json.load(sys.stdin); print(d.get("status"), d.get("verification_quality"))'
```

Two notes:

- The migration is **additive and idempotent** — a second startup on an
  already-migrated DB does not raise. This order is a *tested contract*, not
  prose: `tests/test_startup_migration.py` (`test_startup_migrates_old_schema_db`,
  `test_migration_is_idempotent`) builds an old-schema `runs.db`, runs a fresh
  startup, and asserts the columns exist and `record_run` works immediately.
- Restarting `llama-server` is optional and independent of this order (it is
  stateless w.r.t. `runs.db`). Owner runs the Q8 tower restart himself.

## Test suites

```bash
./scripts/qa_gate.sh                                                  # the ONE gate command (see below)
BASE_URL=http://127.0.0.1:8000 uv run pytest tests/ -m "not gpu" -v   # cheap tier
WRITE_PROBES=1 BASE_URL=... uv run pytest -rxX tests/ -v               # all tiers, throwaway store!
TAG=v0.2.0 BASE_URL=... ./scripts/tag_check.sh                          # mechanical release gate
```

Never point `WRITE_PROBES=1` at a production baseline store — the S1 probes
overwrite baselines by design (that is the defect under test).

### `scripts/qa_gate.sh` — the one command that is actually the gate

Boots its own throwaway healthy + dead-backend instances on split stores and
runs the whole suite bounded (`timeout 300`, per-test `--timeout=45`). Seven
clauses, each born from a real failure:

| Clause | What it enforces | Why |
|---|---|---|
| 1 | `flock` on a lockfile | concurrent orphaned runs contended WAL/baseline locks — the prime suspect for the 240s "hang" |
| 2 | healthy + dead-backend instances, bounded health-poll, log dump on deadline | never wait on an instance that never came up |
| 2b | external deps (llama towers = backends + S2 SSRF target) probed at startup; refuse to run with the remedy printed if absent | on a host without `:8080`, S2/S2b passed vacuously and health tests ConnectErrored mid-suite — an unmet host precondition is a startup error, not a test result |
| 3 | whole gate under `timeout 300`, per-test `--timeout=45` | a gate that can hang is not a gate |
| 4 | all suite env vars exported in one place | half the red gates were harness-assembly errors, not code |
| 5 | `trap` teardown of ports/processes/temp stores on every path | leaked test ports poisoned the next run |
| 6 | **positive controls**: redirector hit-log ≥1, a real `LLAMA_UNREACHABLE` row in the dead store, ≥1 row in the write store | "absence of bad outcome" is not evidence a fault test ran. S2 passed falsely because a refused connection looks identical to a clean probe |
| 7 | **check-then-tag order enforced by construction**: with `CANDIDATE_TAG=vX.Y.Z` set, the gate *ends* by running `tag_check.sh` for that tag and goes red if it fails | a v0.4.2 release was tagged before the ritual ran because nothing made the ordering mechanical |

```bash
CANDIDATE_TAG=v0.4.2 ./scripts/qa_gate.sh   # suite + positive controls + tag ritual, in that order
```

Clause 6's hit-log is opened *after* service start and torn down with the run,
so it can never satisfy itself with evidence from a previous gate run.

## Troubleshooting (field notes, pvellm/Orange Pi ops)

- **`pkill -f '<pattern>'` over ssh can self-kill the session.** If the ssh
  command text itself contains the target command-line literally (e.g.
  `pkill -f "uvicorn app:app --port 8010"`), the pattern matches the remote
  `bash -c` process and you get exit 255 mid-script — orphaning whatever ran
  next. Portable fix: kill by port — `fuser -k 8010/tcp` — or match on
  `pgrep -f` output filtered by PID.
- **Backgrounded uvicorn over ssh holds the session open** until the child's
  stdout/stderr fds close. Launch detached:
  `setsid uv run uvicorn app:app --port 8010 >/tmp/app.log 2>&1 </dev/null &`
  — or use the systemd units above, which are the recommended run mode.
- **SELinux (RHEL-family):** pvellm runs with SELinux **Disabled** (verified
  `getenforce`), so it has never been proven against this app — the
  virgin-VM rehearsal (QA-REPORT-2 follow-up) is the real test. If your box is
  **Enforcing** and something fails (bind, Chromium sandbox, outbound fetch),
  check `ausearch -m avc -ts recent` first; confined setups may need
  `semanage port -a -t http_port_t -p tcp 8000` and
  `setsebool -P httpd_can_network_connect 1`. Binding `127.0.0.1` limits the
  surface (recommended posture until v0.3 auth lands).
- **uv venvs contain no pip.** `python -m pip ...` inside `.venv` fails; use
  `uv sync`, `uv pip`, or `uv run <cmd>`.

## Security posture (v0.1 known limits — read before exposing the port)

- **No authentication.** Anyone who can reach :8000 can overwrite baselines
  (S1) and use the server as a URL fetch reader (S2). Bind to localhost or a
  firewalled interface only until v0.3 lands (`HOST=127.0.0.1 ./scripts/start_app.sh`).
- Confidence scores are VLM prose (advisory only) until the v0.5 calibration
  milestone. Do not wire alerts to them yet.
- Full defect list: see `tests/test_seed_behavior.py` xfail reasons (S-IDs from
  the architecture review).

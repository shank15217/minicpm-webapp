# Deploying minicpm-webapp on a new machine

End-to-end guide: from a bare Linux box to a running synthetic website-health
monitor (Playwright screenshots judged against a MiniCPM-V baseline via llama.cpp).

Two supported distro families: **RHEL-family** (Rocky/Alma/RHEL/Fedora — verified on
Rocky Linux 10.2) and **Debian-family** (Debian/Ubuntu). Everything Python goes
through **uv**; never use `python -m pip` (uv venvs contain no pip — verified on pvellm).

## Architecture recap

```
client ──▶ FastAPI app (port 8000) ──▶ llama-server :8080 (16x mmproj, fast)
                 │                 └──▶ llama-server :8081 (4x mmproj, detail)
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
   hf download openbmb/MiniCPM-V-4.7-1B-gguf MiniCPM-V-4_7-1B-Q4_K_M.gguf --local-dir /opt/models/MiniCPM-V-4.7
   hf download openbmb/MiniCPM-V-4.7-1B-gguf mmproj-model-f16.gguf        --local-dir /opt/models/MiniCPM-V-4.7
   hf download openbmb/MiniCPM-V-4.7-1B-gguf mmproj-model-f16-4x.gguf     --local-dir /opt/models/MiniCPM-V-4.7
   ```

## Step 3 — Start services

Named scripts (each is idempotent; they health-check before starting):

```bash
MODEL_DIR=/opt/models/MiniCPM-V-4.7 ./scripts/start_llama_servers.sh   # :8080 (16x) + :8081 (4x)
./scripts/start_app.sh                                                 # app on :8000
```

App env overrides: `PORT`, `HOST`, `BASELINES_DIR`,
`LLAMA_SERVER_16X`, `LLAMA_SERVER_4X`.

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

## Test suites

```bash
BASE_URL=http://127.0.0.1:8000 uv run pytest tests/ -m "not gpu" -v   # cheap tier
WRITE_PROBES=1 BASE_URL=... uv run pytest -rxX tests/ -v               # all tiers, throwaway store!
TAG=v0.2.0 BASE_URL=... ./scripts/tag_check.sh                          # mechanical release gate
```

Never point `WRITE_PROBES=1` at a production baseline store — the S1 probes
overwrite baselines by design (that is the defect under test).

## Security posture (v0.1 known limits — read before exposing the port)

- **No authentication.** Anyone who can reach :8000 can overwrite baselines
  (S1) and use the server as a URL fetch reader (S2). Bind to localhost or a
  firewalled interface only until v0.3 lands (`HOST=127.0.0.1 ./scripts/start_app.sh`).
- Confidence scores are VLM prose (advisory only) until the v0.5 calibration
  milestone. Do not wire alerts to them yet.
- Full defect list: see `tests/test_seed_behavior.py` xfail reasons (S-IDs from
  the architecture review).

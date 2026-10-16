# MiniCPM-V 4.7 Image Analyzer & Website Health Monitor

A lightweight FastAPI application that uses local **llama.cpp** servers running
the MiniCPM-V-4.7-1B multimodal model to:

- Describe and classify images (documents, screenshots, photos, etc.)
- Perform website health / defacement checks by comparing a live page against a
  known-good baseline — the verdict is owned by a **deterministic pixel diff**
  (dHash64 + MAE per band); the VLM is a *narrator* only
- Support both vision tower modes simultaneously: **`detail`** (full-resolution
  `mmproj-model-f16.gguf`, ~833 prompt tokens per image) and **`fast`**
  (`mmproj-model-f16-4x.gguf`, a 2×2 downsample, ~224 tokens)

The web UI is optional; the entire workflow is available via a REST API.

> **Tower naming, settled by measurement (2026-10-15):** the file named
> `f16` is the high-detail tower and `-4x` is the token-saving one —
> identical 640×480 image → 832 prompt tokens on the `f16` server vs 223 on
> the `-4x` server. Legacy labels `16x`/`4x` still work as aliases
> (`16x`→`detail`, `4x`→`fast`). Earlier revisions of this README called
> "16x" the fast mode and paired the `-4x` tower with port 8080 — that was
> inverted on both counts; corrected here and pinned by the token counts above.

**Start here for testing and shipping:** [docs/RUNBOOK.md](docs/RUNBOOK.md)
(user test plan U1–U6 + published weaknesses ledger W1–W11). Deploy order and
tower/mode contract: [docs/DEPLOY.md](docs/DEPLOY.md). Calibration doctrine:
[docs/calibration-protocol.md](docs/calibration-protocol.md).

## Features

- **Deterministic verdict**: `detdiff.py` computes per-band dHash64+MAE
  similarity vs baseline; all bands ≥ threshold ⇒ OK with **zero VLM calls**.
  The model cannot flip a verdict in either direction.
- **Narrator-only VLM**: only flagged band crops are described, single-image,
  describe-only prompts (no pairwise judgment, no scores — see RUNBOOK W6).
- **Dual vision modes**: `mode=detail|fast` routes to separate llama-server
  instances (f16 tower on `:8080`, 4x tower on `:8081` — the app defaults).
- **Baseline capture**: snapshot a healthy site once; PNGs + `index.json`
  under `baselines/` (single copy — see RUNBOOK W10).
- **Site-updated flow**: all bands differing plus a large height delta routes
  to `verdict=site-updated` (human re-baseline), not a deface alert.
- **Network accessible**: binds `0.0.0.0` — keep it LAN-only until RUNBOOK
  W5 (SSRF defects S2/S2b) closes.
- **Audit trail**: every check logged to `baselines/runs.db` with raw and
  aligned per-band similarity vectors.

## Architecture

```
┌─────────────┐     ┌──────────────────┐     ┌─────────────────────┐
│  Client /   │────▶│  FastAPI (app.py)│────▶│ llama-server :8080  │
│  UI / curl  │     │  port 8000       │     │  detail (f16 tower) │
└─────────────┘     │                  │     └─────────────────────┘
                    │  detdiff (owns   │     ┌─────────────────────┐
                    │  the verdict) +  │────▶│ llama-server :8081  │
                    │  Playwright      │     │  fast (-4x tower)   │
                    └──────────────────┘     └─────────────────────┘
```

- `app.py` — FastAPI server, capture, baseline management, orchestration.
- `detdiff.py` — deterministic comparison (the judge; no GPU, no model).
- `anchorcheck.py` — single-image content-anchor checklist (v0.5.5,
  qualified-to-build; not certified to alert).
- `static/index.html` — Minimal web UI.
- `baselines/` — Persistent storage for captured healthy screenshots.
- External: two llama-server processes + Playwright Chromium.

## Prerequisites

1. **llama.cpp** built from a checkout that includes MiniCPM-V 4.7 support (PR #29416 or later).
2. Model files from Hugging Face (`openbmb/MiniCPM-V-4.7-1B-gguf`):
   - `MiniCPM-V-4_7-1B-Q8_0.gguf` (**default since v0.4**; `Q4_K_M` documented as rollback)
   - `mmproj-model-f16.gguf` (the detail tower)
   - `mmproj-model-f16-4x.gguf` (the fast tower)

   ```bash
   huggingface-cli download openbmb/MiniCPM-V-4.7-1B-gguf MiniCPM-V-4_7-1B-Q8_0.gguf --local-dir models
   huggingface-cli download openbmb/MiniCPM-V-4.7-1B-gguf mmproj-model-f16.gguf --local-dir models
   huggingface-cli download openbmb/MiniCPM-V-4.7-1B-gguf mmproj-model-f16-4x.gguf --local-dir models
   ```

3. Python 3.10+ with the packages in `requirements.txt` (or `uv sync --extra dev`).

## Quick Start

> **Recommended: fresh machine?** Run `sudo ./scripts/install.sh` (idempotent) —
> it installs RHEL/Debian system packages for headless Chromium, `uv`, the
> locked venv (`uv sync`), and Playwright Chromium, with a launch smoke test.
> Full walkthrough incl. llama.cpp, models, and systemd: **[docs/DEPLOY.md](docs/DEPLOY.md)**.
> Easiest path: `scripts/start_llama_servers.sh` starts both towers with the
> correct mmproj↔port pairing and preserves anything already running.

### 1. Start the two llama-server instances

Port/mode pairing **matches the app defaults** — do not cross them
(`mode=detail` requests go to `:8080`; launching the `-4x` tower there
silently degrades detail-mode checks — an earlier revision of this README
suggested exactly that, which is how the labels got trusted over the
physics):

```bash
# detail — full-resolution f16 tower — port 8080  (default mode)
/AITOOLCHAIN/llama.cpp/build/bin/llama-server \
  -m /AITOOLCHAIN/models/MiniCPM-V-4.7/MiniCPM-V-4_7-1B-Q8_0.gguf \
  --mmproj /AITOOLCHAIN/models/MiniCPM-V-4.7/mmproj-model-f16.gguf \
  -c 16384 \
  -fa on \
  --reasoning on \
  --jinja \
  --chat-template-kwargs '{"preserve_thinking":true}' \
  --port 8080 \
  --host 0.0.0.0 \
  -lv 4

# fast — token-saving -4x tower — port 8081
/AITOOLCHAIN/llama.cpp/build/bin/llama-server \
  -m /AITOOLCHAIN/models/MiniCPM-V-4.7/MiniCPM-V-4_7-1B-Q8_0.gguf \
  --mmproj /AITOOLCHAIN/models/MiniCPM-V-4.7/mmproj-model-f16-4x.gguf \
  -c 16384 \
  -fa on \
  --reasoning on \
  --jinja \
  --chat-template-kwargs '{"preserve_thinking":true}' \
  --port 8081 \
  --host 0.0.0.0 \
  -lv 4
```

Notes on these flags:
- `-fa on` — flash attention
- `--reasoning on --jinja --chat-template-kwargs '{"preserve_thinking":true}'` — enable thinking (generation budget lives in the app's narration call, `max_tokens=1200`)
- `-c 16384` — context size with headroom for images
- `-lv 4` — verbose logging

### 2. Install and run the analyzer

#### Option A: UV (recommended for agent iteration)

```bash
cd minicpm-webapp
uv venv
source .venv/bin/activate
uv pip install -r requirements.txt
playwright install chromium
python app.py
```

UV creates a `.venv/` directory that should be gitignored. The rest of the project (source, README, static files, requirements.txt) commits normally, so agents can clone the repo and recreate the environment with the same commands.

#### Option B: Standard pip

```bash
cd minicpm-webapp
pip install -r requirements.txt
playwright install chromium
python app.py
```

Open `http://<your-ip>:8000`.

### Git notes for agents

Add to `.gitignore`:

```gitignore
.venv/
__pycache__/
*.pyc
baselines/*.png
baselines/index.json
```

This keeps the virtual environment and runtime baseline data out of the repo —
but note that makes baselines a single unbacked copy (RUNBOOK W10): back
`baselines/` up (restic/rsync + sha256 manifest) or accept that disk loss
resets every comparison, profile, and attestation to zero.

### Environment variables

```bash
LLAMA_SERVER_16X   # default http://127.0.0.1:8080/v1/chat/completions — detail (f16) tower
LLAMA_SERVER_4X    # default http://127.0.0.1:8081/v1/chat/completions — fast (-4x) tower
```

Defaults match the launch commands above; set them only when the towers run
on a different host. (Variable names keep the legacy `16X/4X` spelling;
semantically `16X`=detail, `4X`=fast.)

## Usage

### Capture a baseline
1. Enter the URL of a healthy site.
2. Check **Capture as baseline**.
3. Submit. A full-page screenshot is stored in `baselines/` and linked to the URL.

### Later health check
1. Enter the same URL (image upload is optional).
2. The app diffs a fresh screenshot against the stored baseline
   deterministically: unchanged ⇒ `status=OK`, no VLM call at all; changed ⇒
   flagged bands get single-image narration, and `det_changed_bands` /
   `det_band_sims` land in `run_history`.

You can still upload an arbitrary reference image at any time; the stored baseline is preferred only when one exists for the given URL.

## API Reference

All endpoints return JSON.

### `POST /analyze`
Multipart form-data (`-F`, not JSON).

| Field              | Required | Default      | Description |
|--------------------|----------|--------------|-------------|
| `file`             | No       | —            | Reference image (optional if baseline exists) |
| `url`              | No*      | —            | Webpage URL |
| `mode`             | No       | `"detail"`   | `"detail"` / `"fast"` (legacy aliases `16x`/`4x`) |
| `visual_verify`    | No       | `"true"`     | Capture live screenshot for comparison |
| `capture_baseline` | No       | `"false"`    | Capture and store live screenshot as baseline |
| `prompt`           | No       | —            | Custom instruction for the initial description |

\*A URL is required for baseline capture and for verification.

**Example – capture baseline**
```bash
curl -X POST http://localhost:8000/analyze \
  -F "url=https://example.com" \
  -F "capture_baseline=true" \
  -F "mode=detail"
```

**Example – check against baseline**
```bash
curl -X POST http://localhost:8000/analyze \
  -F "url=https://example.com" \
  -F "visual_verify=true" \
  -F "mode=detail"
```

### `GET /baselines`
Returns the current baseline index (URL → metadata).

### `GET /health`
Returns status of both llama-servers, Playwright availability, and baseline count.

## Storage

```
baselines/
├── index.json          # URL → {path, captured_at, ...}
├── runs.db             # audit trail (SQLite; rows grow per check — see W11 + scripts prune)
└── <url-hash>.png      # Full-page screenshots (baselines)
```

Delete the directory or individual files to reset baselines (back up first — W10).

## Limitations & Notes

Short version — the full ledger with receipts and measured numbers lives in
**[docs/RUNBOOK.md](docs/RUNBOOK.md) §3 (W1–W11)**. Highlights:

- Sub-~1%-area edits pass the pixel threshold (W1).
- Dynamic (rotating) bands are pixel-noisy in **both** directions; do not page
  anyone on a single dynamic-band alert until tolerance profiles graduate (W2).
  Overlay defacement on a dynamic band passes both current channels (W3) —
  high-dynamic bands ship masking-with-flag → human review.
- Threshold `0.995` is provisional until §2 calibration profiles land (W4).
- Open SSRF defects (S2/S2b): **LAN-only deployment** (W5).
- Narration is advisory by design; verdicts come from `detdiff`, never from
  the model (W6).
- Nothing is monitored automatically until the v0.5 scheduler units or your
  own cron are installed (W11).
- Designed for **unauthenticated public pages**; heavy bot protection or login
  walls may fail capture (labeled failure, never silently green).
- No authentication on the FastAPI server itself — reverse proxy or
  middleware before anything beyond a trusted network.

## Future Ideas

- Multi-page or multi-URL batch verification
- Screenshot diff visualization
- Support for the 35B-A3B model (needs ≥ a card that fits it; 16GB nodes don't)
- Optional auth and rate limiting
- Text-inventory channel for dynamic-band defacement (v0.6 slate, own mini-cal)

## Project Structure

```
minicpm-webapp/
├── app.py              # FastAPI application
├── detdiff.py          # deterministic comparison (the verdict owner)
├── anchorcheck.py      # content-anchor checklist (v0.5.5, build-qualified)
├── scripts/            # qa_gate.sh, tag_check.sh, calibrate.py, start_*
├── tests/              # seed-behavior pins, migration, calibrate, monitor
├── docs/               # DEPLOY.md, RUNBOOK.md, calibration-protocol.md
├── requirements.txt
├── static/
│   └── index.html      # Simple web UI
└── baselines/          # Created at runtime (back this up — W10)
    ├── index.json
    └── *.png
```

## License

This harness is provided as-is for local use with the MiniCPM-V 4.7 model (Apache 2.0).

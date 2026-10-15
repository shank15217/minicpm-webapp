# MiniCPM-V 4.7 Image Analyzer & Website Health Monitor

A lightweight FastAPI application that uses a local **llama.cpp** server running the MiniCPM-V-4.7-1B multimodal model to:

- Describe and classify images (documents, screenshots, photos, etc.)
- Perform website health / defacement checks by comparing a live page against a known-good baseline
- Support both the **16x** (faster, fewer visual tokens) and **4x** (higher detail) vision tower modes simultaneously

The web UI is optional; the entire workflow is available via a clean REST API.

## Features

- **Dual vision modes**: Route requests to separate llama-server instances (16x and 4x mmproj).
- **Baseline capture**: Snapshot a healthy site once and associate the screenshot with a URL.
- **Visual + text verification**: Compare a live screenshot (Playwright) against the stored baseline (or an uploaded reference image). Returns a 0–100 confidence score, difference description, and verdict.
- **Network accessible**: Binds to `0.0.0.0` by default.
- **Simple persistent storage**: Baselines stored as PNGs + `index.json` under `baselines/`.
- **OpenAI-compatible backend**: Talks to llama-server’s `/v1/chat/completions` endpoint.

## Architecture

```
┌─────────────┐     ┌──────────────────┐     ┌─────────────────────┐
│  Client /   │────▶│  FastAPI (app.py)│────▶│ llama-server :8080  │
│  UI / curl  │     │  port 8000       │     │  (16x mmproj)       │
└─────────────┘     │                  │     └─────────────────────┘
                    │  Playwright      │     ┌─────────────────────┐
                    │  baselines/      │────▶│ llama-server :8081  │
                    └──────────────────┘     │  (4x mmproj)        │
                                             └─────────────────────┘
```

- `app.py` — FastAPI server, image handling, baseline management, verification logic.
- `static/index.html` — Minimal web UI.
- `baselines/` — Persistent storage for captured healthy screenshots.
- External: two llama-server processes + Playwright Chromium.

## Prerequisites

1. **llama.cpp** built from a checkout that includes MiniCPM-V 4.7 support (PR #29416 or later).
2. Model files from Hugging Face (`openbmb/MiniCPM-V-4.7-1B-gguf`):
   - `MiniCPM-V-4_7-1B-Q4_K_M.gguf` (or another quant)
   - `mmproj-model-f16.gguf` (16x)
   - `mmproj-model-f16-4x.gguf` (4x)

   ```bash
   huggingface-cli download openbmb/MiniCPM-V-4.7-1B-gguf MiniCPM-V-4_7-1B-Q4_K_M.gguf --local-dir models
   huggingface-cli download openbmb/MiniCPM-V-4.7-1B-gguf mmproj-model-f16.gguf --local-dir models
   huggingface-cli download openbmb/MiniCPM-V-4.7-1B-gguf mmproj-model-f16-4x.gguf --local-dir models
   ```

3. Python 3.10+ with the packages in `requirements.txt`.

## Quick Start

### 1. Start the two llama-server instances

Example using the flags and paths currently in use:

```bash
# 4x (more detail) — port 8080
/AITOOLCHAIN/llama.cpp/build/bin/llama-server \
  -m /AITOOLCHAIN/models/MiniCPM-V-4.7/MiniCPM-V-4_7-1B-Q4_K_M.gguf \
  --mmproj /AITOOLCHAIN/models/MiniCPM-V-4.7/mmproj-model-f16-4x.gguf \
  -c 16384 \
  -fa on \
  --reasoning on \
  --jinja \
  --chat-template-kwargs '{"preserve_thinking":true}' \
  --port 8080 \
  --host 0.0.0.0 \
  -lv 4

# 16x (faster, default) — port 8081
/AITOOLCHAIN/llama.cpp/build/bin/llama-server \
  -m /AITOOLCHAIN/models/MiniCPM-V-4.7/MiniCPM-V-4_7-1B-Q4_K_M.gguf \
  --mmproj /AITOOLCHAIN/models/MiniCPM-V-4.7/mmproj-model-f16.gguf \
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
- `--reasoning on --jinja --chat-template-kwargs '{"preserve_thinking":true}'` — enable thinking
- `-c 16384` — context size with headroom for images
- `-lv 4` — verbose logging
- Ports are swapped relative to the FastAPI defaults (4x on 8080, 16x on 8081). Set the env vars below accordingly.

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

This keeps the virtual environment and runtime baseline data out of the repo while leaving everything else ready for iteration.

### Environment variables

The FastAPI app defaults assume 16x on port 8080 and 4x on port 8081. With the launch commands above (4x on 8080, 16x on 8081), set:

```bash
export LLAMA_SERVER_16X="http://127.0.0.1:8081/v1/chat/completions"
export LLAMA_SERVER_4X="http://127.0.0.1:8080/v1/chat/completions"
python app.py
```

Adjust the host if the servers are on a different machine.

## Usage

### Capture a baseline
1. Enter the URL of a healthy site.
2. Check **Capture as baseline**.
3. Submit. A full-page screenshot is stored in `baselines/` and linked to the URL.

### Later health check
1. Enter the same URL (image upload is optional).
2. Leave **Visual verification** checked.
3. The app loads the stored baseline, takes a fresh screenshot, and returns a comparison with confidence score and differences.

You can still upload an arbitrary reference image at any time; the stored baseline is preferred only when one exists for the given URL.

## API Reference

All endpoints return JSON.

### `POST /analyze`
Multipart form-data.

| Field              | Required | Default   | Description |
|--------------------|----------|-----------|-------------|
| `file`             | No       | —         | Reference image (optional if baseline exists) |
| `url`              | No*      | —         | Webpage URL |
| `mode`             | No       | `"16x"`   | `"16x"` or `"4x"` |
| `visual_verify`    | No       | `"true"`  | Capture live screenshot for comparison |
| `capture_baseline` | No       | `"false"` | Capture and store live screenshot as baseline |
| `prompt`           | No       | —         | Custom instruction for the initial description |

\*A URL is required for baseline capture and for verification.

**Example – capture baseline**
```bash
curl -X POST http://localhost:8000/analyze \
  -F "url=https://example.com" \
  -F "capture_baseline=true" \
  -F "mode=16x"
```

**Example – check against baseline**
```bash
curl -X POST http://localhost:8000/analyze \
  -F "url=https://example.com" \
  -F "visual_verify=true" \
  -F "mode=4x"
```

### `GET /baselines`
Returns the current baseline index (URL → metadata).

### `GET /health`
Returns status of both llama-servers, Playwright availability, and baseline count.

## Storage

```
baselines/
├── index.json          # URL → {path, captured_at, ...}
└── <url-hash>.png      # Full-page screenshot
```

Delete the directory or individual files to reset baselines.

## Limitations & Notes

- Designed for **unauthenticated public pages**. Sites with heavy bot protection or login walls may fail screenshot capture (falls back to text-only comparison).
- 4x mode uses significantly more context and is slower; increase `-c` on the llama-server if needed.
- The 1B model is intentionally lightweight so both instances can run side-by-side on modest hardware.
- Confidence scoring is model-generated (heuristic). For production alerting, sample results and tune thresholds.
- No authentication on the FastAPI server itself — add a reverse proxy or middleware if exposing beyond a trusted network.

## Future Ideas

- Scheduled / cron-driven checks with alerting
- Multi-page or multi-URL batch verification
- Screenshot diff visualization
- Support for the 35B-A3B model
- Optional auth and rate limiting
- Export of historical check results

## Project Structure

```
minicpm-webapp/
├── app.py              # FastAPI application
├── requirements.txt
├── README.md
├── static/
│   └── index.html      # Simple web UI
└── baselines/          # Created at runtime
    ├── index.json
    └── *.png
```

## License

This harness is provided as-is for local use with the MiniCPM-V 4.7 model (Apache 2.0).

import asyncio
import base64
from contextlib import asynccontextmanager
import functools
import hashlib
import io
import json
import os
import re
from pathlib import Path
import sqlite3
import time
import datetime
from typing import Optional

import anyio
import detdiff
import httpx
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Client is created per running app instance (per event loop), not at
    module import — survives TestClient/per-test-loop usage (Sammy finding 2)."""
    global _http
    _http = httpx.AsyncClient(
        timeout=httpx.Timeout(180.0, connect=10.0), follow_redirects=True
    )
    init_run_db()
    try:
        yield
    finally:
        await _http.aclose()


app = FastAPI(title="MiniCPM-V Image Analyzer", lifespan=lifespan)

# Serve static files (HTML)
app.mount("/static", StaticFiles(directory="static"), name="static")

# Baselines storage
BASELINES_DIR = Path(os.getenv("BASELINES_DIR", "baselines"))
BASELINES_DIR.mkdir(parents=True, exist_ok=True)
INDEX_PATH = BASELINES_DIR / "index.json"

# Two possible backends. Override via env if needed.
LLAMA_SERVER_16X = os.getenv(
    "LLAMA_SERVER_16X",
    "http://127.0.0.1:8080/v1/chat/completions"
)
LLAMA_SERVER_4X = os.getenv(
    "LLAMA_SERVER_4X",
    "http://127.0.0.1:8081/v1/chat/completions"
)
# v0.4 deterministic verdict layer (detdiff): page_sim at or above this
# threshold => "identical", VLM narration skipped entirely. Provisional
# pending v0.5 control calibration (per-baseline, per-band profiles).
DET_PASS_SIM = float(os.getenv("DET_PASS_SIM", "0.995"))

SYSTEM_PROMPT = """You are a precise image analysis assistant. 
When given an image, provide:
1. A detailed description of what you see.
2. A classification/category (e.g., "document", "photograph", "screenshot of webpage", "chart", "diagram", "receipt", "ID card", etc.).
3. If it appears to be a webpage screenshot or document, note any visible URL, title, or key content.

Be concise but informative. Respond in a structured way."""

# VERIFY_PROMPT_TEMPLATE (pairwise 0-100 + Verdict prompt) was DELETED here
# in v0.4.2 (S10 close, Sammy catch / Quinn pin e1f639a): every branch that
# formatted it either runs single-image narration now or never reaches the
# model. It also embedded unframed site page_text — the last S3-class prompt-
# injection sink into model input. Do NOT reintroduce any pairwise prompt:
# tests/test_seed_behavior.py::S10 asserts this string never returns.

# v0.4.2 single-image narrator prompt. Pairwise "same-or-different?" is
# OUT of the VLM: measured laundering — the 1B on the high-res tower
# called a red-defacement crop pair "identical … score 100, Healthy"
# (QA-REPORT-6 e2e, Q8/f16). The deterministic diff supplies the
# comparison; the narrator only describes what is actually there, a
# question it answers honestly. Corpus change => new PROMPT_VERSION:
# run-history rows predating this are not narration-comparable, by design.
NARRATION_PROMPT_SINGLE = """You are given one or more screenshot regions of a live webpage. For EACH region, describe factually: the main text, layout elements, colors, and any anomalies (blank areas, unexpected banners, error text, garbled content). Do NOT assign health scores or verdicts. Be specific and concise."""

# Version stamp over the prompt corpus: any edit to the prompt text changes
# this hash, and run-history rows on different prompt_version values are NOT
# score-comparable (Sammy provenance requirement #1, 2026-10-15).
PROMPT_VERSION = hashlib.sha256(
    (SYSTEM_PROMPT + NARRATION_PROMPT_SINGLE).encode()
).hexdigest()[:12]

# Tower semantics: `detail` = full-resolution tower (mmproj-model-f16,
# ~837 visual tokens on 640x480), `fast` = token-saving downsample tower
# (mmproj-model-f16-4x, ~228 tokens). `16x`/`4x` are DEPRECATED aliases
# (owner ruling 2026-10-15; naming debate settled by the same token
# measurements — see start_llama_servers.sh header).
MODE_ALIASES = {"detail": "detail", "fast": "fast", "16x": "detail", "4x": "fast"}


class CheckFailed(Exception):
    """A monitoring check could not be performed (infrastructure failure).

    NEVER convertible into page/VLM content: endpoints translate this into a
    5xx CHECK_FAILED response so 'check failed' can never masquerade as
    'site unhealthy' (defect S6) or feed error prose to the model (S6b).
    """

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


# ---------------------------------------------------------------------------
# Run history (SQLite) — answers "was it healthy at 3am?" and keeps every
# score comparable via full provenance (Sammy requirement #1): model, prompt
# version, backend, llama.cpp build fingerprint, finish_reason and the raw
# model output are stored per row.
# ---------------------------------------------------------------------------
RUNS_DB_PATH = BASELINES_DIR / "runs.db"

RUNS_SCHEMA = """
CREATE TABLE IF NOT EXISTS run_history (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                TEXT NOT NULL,           -- ISO-8601 UTC
    url               TEXT,
    baseline_key      TEXT,
    baseline_sha256   TEXT,                    -- digest of compared baseline PNG
    mode              TEXT,                    -- detail / fast (legacy: 16x / 4x)
    backend_url       TEXT,                    -- llama-server used
    model             TEXT,                    -- model path reported by backend
    llama_fingerprint TEXT,                    -- llama.cpp system_fingerprint
    prompt_version    TEXT,                    -- sha256[:12] of prompt corpus
    status            TEXT NOT NULL,           -- OK | CHECK_FAILED
    error_code        TEXT,                    -- when CHECK_FAILED
    finish_reason     TEXT,                    -- stop|length|content_filter|...
    score             INTEGER,                 -- NULL until v0.5 structured output
    verdict           TEXT,                    -- v0.5: DERIVED from score, never model's own
    kind              TEXT DEFAULT 'check',    -- check | control (calibration identical-input runs)
    det_similarity    REAL,                    -- v0.4: deterministic page_sim (THE verdict)
    det_changed_bands TEXT,                    -- JSON list of "y0-y1" windows below threshold
    det_band_sims     TEXT,                    -- v0.5: RAW per-band sim vector (alert channel)
    det_aligned_band_sims TEXT,                -- v0.5: (b)-lite aligned vector (localization/tolerance only)
    raw_output        TEXT,                    -- full model text, for audit
    page_text_sha256  TEXT,                    -- what text context the model saw
    elapsed_s         REAL
);
CREATE INDEX IF NOT EXISTS idx_run_history_ts ON run_history (ts);
CREATE INDEX IF NOT EXISTS idx_run_history_url ON run_history (url);
"""


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(RUNS_DB_PATH)
    # WAL: readers never block on the single writer, and fsync is cheap.
    # busy_timeout: concurrent writers wait instead of raising
    # 'database is locked' — a completed check must never 500 on history
    # bookkeeping (Sammy finding 1; scheduled checks in v0.3+ create
    # concurrent writers).
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=2000")
    return conn


def init_run_db() -> None:
    with _db() as conn:
        conn.executescript(RUNS_SCHEMA)
        # CREATE TABLE IF NOT EXISTS never adds columns to a table that
        # already exists — explicit idempotent migration for older run DBs.
        existing = {r[1] for r in conn.execute("PRAGMA table_info(run_history)")}
        for col, typ in (("kind", "TEXT DEFAULT 'check'"),
                         ("det_similarity", "REAL"),
                         ("det_changed_bands", "TEXT"),
                         # v0.5 calibration protocol §6: per-band vectors so
                         # tolerance profiles are a query, not a spreadsheet.
                         # RAW vector feeds alerts; ALIGNED vector is
                         # localization/tolerance only (guardrail §6.5).
                         ("det_band_sims", "TEXT"),
                         ("det_aligned_band_sims", "TEXT")):
            if col not in existing:
                conn.execute(f"ALTER TABLE run_history ADD COLUMN {col} {typ}")


def record_run(**fields) -> None:
    """Insert one run-history row. Small local write; kept sync by design."""
    row = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
        **fields,
    }
    cols = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    with _db() as conn:
        conn.execute(f"INSERT INTO run_history ({cols}) VALUES ({marks})", tuple(row.values()))


def url_to_hash(url: str) -> str:
    """Stable short hash for a URL used as baseline key."""
    return hashlib.sha256(url.strip().encode()).hexdigest()[:16]


def load_index() -> dict:
    if INDEX_PATH.exists():
        try:
            return json.loads(INDEX_PATH.read_text())
        except Exception:
            return {"baselines": {}}
    return {"baselines": {}}


def save_index(index: dict):
    INDEX_PATH.write_text(json.dumps(index, indent=2))


def save_baseline(url: str, image_bytes: bytes, description: Optional[str] = None) -> str:
    """Save a baseline screenshot associated with the URL. Returns the hash key."""
    key = url_to_hash(url)
    path = BASELINES_DIR / f"{key}.png"
    path.write_bytes(image_bytes)
    index = load_index()
    index["baselines"][key] = {
        "url": url.strip(),
        "path": str(path),
        "captured_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
        "description": description or "",
    }
    save_index(index)
    return key


def load_baseline(url: str) -> Optional[dict]:
    """Load stored baseline metadata (including image path and description) for a URL, or None if missing."""
    key = url_to_hash(url)
    index = load_index()
    entry = index.get("baselines", {}).get(key)
    if not entry:
        return None
    path = Path(entry["path"])
    if not path.exists():
        return None
    entry["image_bytes"] = path.read_bytes()
    return entry


def image_to_base64(image_bytes: bytes, max_size: int = 1280) -> str:
    """Convert image bytes to base64 data URL."""
    try:
        img = Image.open(io.BytesIO(image_bytes))
        if max(img.size) > max_size:
            img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            image_bytes = buf.getvalue()
    except Exception:
        pass

    b64 = base64.b64encode(image_bytes).decode("utf-8")
    return f"data:image/png;base64,{b64}"


def narration_crops(ref_bytes: bytes, live_bytes: bytes, det: dict,
                    max_crops: int = 3) -> list:
    """Changed-region crops for the narrator: (label, baseline_url, live_url).

    Sammy/Quinn architecture finding: a full f16-tower page is a huge prompt
    (837 visual tokens for 640x480; full 1280x7463 pages dominate the
    truncation story) and pairwise same-or-different is corrupted anyway.
    The narrator gets ONLY the bands the deterministic diff flagged, worst
    first, up to max_crops — same pixels a human would open the alert for,
    ~10% of the token cost per crop.
    """
    bands = det.get("changed_bands") or []
    # Crop ORDER is a localization decision, so it may use the (b)-lite
    # aligned vector (calibration §6.5 guardrail: aligned sims select crops
    # and tolerances, never innocence — the flag list itself is raw-only).
    sims = det.get("aligned_band_sims") or det.get("band_sims") or []
    ordered = sorted(range(len(bands)), key=lambda i: sims[i] if i < len(sims) else 0.0)
    try:
        a = Image.open(io.BytesIO(ref_bytes))
        b = Image.open(io.BytesIO(live_bytes))
    except Exception:
        return []
    out = []
    for i in ordered[:max_crops]:
        try:
            y0, y1 = bands[i].split("-")
            y0, y1 = int(y0), int(y1)
        except (ValueError, AttributeError):
            continue
        crops = []
        for img in (a, b):
            h = min(y1, img.size[1])
            if h <= y0:
                crops = []
                break
            c = img.crop((0, y0, img.size[0], h))
            buf = io.BytesIO()
            c.convert("RGB").save(buf, format="PNG")
            # max_size=1400: bands are 1280x<=1344; no crush needed.
            crops.append(image_to_base64(buf.getvalue(), max_size=1400))
        if crops:
            out.append((bands[i], crops[0], crops[1]))
    return out


# Shared async HTTP client; created in lifespan startup (per event loop),
# closed on shutdown. Not constructed at module import time.
_http: Optional[httpx.AsyncClient] = None


async def _record(**fields) -> None:
    """Thread-pooled run-history insert: fsync happens off the event loop so
    a slow disk or WAL writer collision can never stall request handling
    (Sammy finding 1)."""
    await anyio.to_thread.run_sync(functools.partial(record_run, **fields))


async def call_llama(messages: list, mode: str = "detail") -> dict:
    """Call the appropriate llama-server. Returns a dict with:
    content, finish_reason, fingerprint, model, backend_url.

    Raises CheckFailed on ANY infrastructure failure (S6) — never returns
    error prose as a result string. finish_reason == 'length' is a failure:
    truncated output must never be parsed downstream (Sammy rule (a)).
    """
    # Resolve semantic mode (detail/fast) or legacy alias (16x/4x) to a
    # backend. `detail`->16x server (f16, high-res); `fast`->4x server (coarse).
    server_url = LLAMA_SERVER_4X if MODE_ALIASES.get(mode, "detail") == "fast" else LLAMA_SERVER_16X
    payload = {
        "model": "minicpm-v",
        "messages": messages,
        "max_tokens": 1200,
        "temperature": 0.2,
        "stream": False,
    }
    try:
        resp = await _http.post(server_url, json=payload)
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPStatusError as e:
        raise CheckFailed("LLAMA_HTTP", f"({mode}) {server_url} -> HTTP {e.response.status_code}")
    except Exception as e:
        raise CheckFailed(
            "LLAMA_UNREACHABLE",
            f"({mode}) {server_url}: {type(e).__name__}: {e}. Is the {mode} server running?",
        )
    try:
        choice = data["choices"][0]
        content = choice["message"]["content"]
        finish_reason = choice.get("finish_reason")
    except (KeyError, IndexError) as e:
        raise CheckFailed("LLAMA_MALFORMED", f"({mode}) response missing fields: {e!r}")
    if finish_reason == "length":
        # Never attempt to use/parse truncated output.
        raise CheckFailed("LLAMA_TRUNCATED", f"({mode}) max_tokens exhausted before stop")
    if not content or not content.strip():
        raise CheckFailed("LLAMA_EMPTY", f"({mode}) empty completion (finish_reason={finish_reason})")
    return {
        "content": content,
        "finish_reason": finish_reason,
        "fingerprint": data.get("system_fingerprint", ""),
        "model": data.get("model", ""),
        "backend_url": server_url,
        "usage": data.get("usage") or {},
    }


async def fetch_page_text(url: str) -> str:
    """Fetch plain text content from a URL for comparison.

    Raises CheckFailed on any failure (S6b) — the old 'Failed to fetch ...'
    string must never be handed to the VLM as page content.
    """
    try:
        resp = await _http.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; MiniCPM-Analyzer/1.0)"},
            timeout=15.0,
        )
        resp.raise_for_status()
        text = resp.text
    except Exception as e:
        raise CheckFailed("FETCH_FAILED", f"{url}: {type(e).__name__}: {e}")
    text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:2000]


async def capture_screenshot(url: str) -> Optional[bytes]:
    """Capture a full-page screenshot of the URL using Playwright (async). Returns PNG bytes or None."""
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ],
            )
            context = await browser.new_context(
                viewport={"width": 1280, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
                locale="en-US",
            )
            # Reduce obvious automation signals
            await context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )
            page = await context.new_page()
            # load is more reliable than networkidle on busy / protected sites
            await page.goto(url, wait_until="load", timeout=60000)
            await page.wait_for_timeout(2500)
            screenshot_bytes = await page.screenshot(full_page=True, type="png")
            await browser.close()
            return screenshot_bytes
    except Exception as e:
        print(f"Screenshot failed for {url}: {e}")
        return None


@app.get("/", response_class=HTMLResponse)
async def root():
    """Serve the simple web UI."""
    html_path = os.path.join("static", "index.html")
    if os.path.exists(html_path):
        with open(html_path, "r") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>UI not found. Check static/index.html</h1>")


@app.post("/analyze")
async def analyze(
    file: Optional[UploadFile] = File(None),
    url: Optional[str] = Form(None),
    prompt: Optional[str] = Form(None),
    mode: Optional[str] = Form("detail"),
    visual_verify: Optional[str] = Form("true"),
    capture_baseline: Optional[str] = Form("false"),
):
    """
    Analyze an image and/or verify a URL.
    - capture_baseline=true + url: take live screenshot and store it as the healthy baseline for that URL.
    - Otherwise: compare live page against stored baseline (if any) or against an uploaded reference image.
    mode: "detail" (full-res tower) or "fast" (token-saving tower);
          legacy aliases "16x"->detail, "4x"->fast still accepted.
    visual_verify: whether to capture a live screenshot for comparison.
    """
    mode = (mode or "detail").lower()
    # Semantic names preferred; `16x`/`4x` accepted as deprecated aliases.
    mode = MODE_ALIASES.get(mode, "detail")

    start_time = time.perf_counter()

    do_visual = visual_verify and visual_verify.lower() in ("true", "1", "yes", "on")
    do_capture = capture_baseline and capture_baseline.lower() in ("true", "1", "yes", "on")

    result = {"mode": mode}

    # Capture baseline mode
    if do_capture:
        if not url or not url.strip():
            return JSONResponse({"error": "URL is required to capture a baseline"}, status_code=400)
        url = url.strip()
        screenshot_bytes = await capture_screenshot(url)
        if not screenshot_bytes:
            # Seed pin (test_capture_path_dns_failure_is_500): keep 500 here;
            # v0.2 adds a machine-readable reason code alongside it.
            return JSONResponse(
                {"status": "CHECK_FAILED", "error_code": "TARGET_UNREACHABLE",
                 "error": "Failed to capture screenshot for baseline"},
                status_code=500,
            )

        # Generate description once and persist it with the baseline
        data_url = image_to_base64(screenshot_bytes)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": [
                {"type": "text", "text": prompt or "Describe this baseline screenshot of the site."},
                {"type": "image_url", "image_url": {"url": data_url}},
            ]},
        ]
        try:
            llm = await call_llama(messages, mode=mode)
        except CheckFailed as e:
            await _record(url=url, mode=mode, status="CHECK_FAILED", error_code=e.code,
                       elapsed_s=round(time.perf_counter() - start_time, 2))
            return JSONResponse(
                {"status": "CHECK_FAILED", "error_code": e.code, "detail": str(e)},
                status_code=502,
            )
        description = llm["content"]

        key = save_baseline(url, screenshot_bytes, description=description)
        await _record(
            url=url, baseline_key=key,
            baseline_sha256=hashlib.sha256(screenshot_bytes).hexdigest(),
            mode=mode, backend_url=llm["backend_url"], model=llm["model"],
            llama_fingerprint=llm["fingerprint"], prompt_version=PROMPT_VERSION,
            status="OK", finish_reason=llm["finish_reason"], raw_output=description,
            elapsed_s=round(time.perf_counter() - start_time, 2),
        )
        result["baseline_captured"] = True
        result["baseline_key"] = key
        result["url"] = url
        result["message"] = f"Baseline captured and associated with {url}. Future checks will compare against this snapshot."
        result["description"] = description
        result["elapsed_seconds"] = round(time.perf_counter() - start_time, 2)
        return JSONResponse(result)

    # Normal analysis / verification path
    image_bytes = None
    if file and file.filename:
        image_bytes = await file.read()

    if not image_bytes and not (url and url.strip()):
        return JSONResponse({"error": "Provide an image and/or a URL"}, status_code=400)

    # Prefer stored baseline as reference if URL is given and baseline exists
    ref_bytes = None
    stored_description = None
    using_baseline = False
    key_for_history = None
    if url and url.strip():
        url = url.strip()
        stored = load_baseline(url)
        if stored:
            ref_bytes = stored["image_bytes"]
            stored_description = stored.get("description") or None
            using_baseline = True
            key_for_history = url_to_hash(url)
            result["using_stored_baseline"] = True

    if not ref_bytes and image_bytes:
        ref_bytes = image_bytes

    if not ref_bytes:
        return JSONResponse({"error": "No reference image available (upload one or capture a baseline first)"}, status_code=400)

    ref_data_url = image_to_base64(ref_bytes)
    result["filename"] = file.filename if file else "stored-baseline"

    # Use stored description if available; otherwise generate one
    if stored_description:
        result["description"] = stored_description
        result["description_source"] = "stored"
    else:
        user_content = [
            {"type": "text", "text": prompt or "Describe this reference image in detail and classify what type of content it is."},
            {"type": "image_url", "image_url": {"url": ref_data_url}},
        ]
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]
        try:
            llm = await call_llama(messages, mode=mode)
        except CheckFailed as e:
            await _record(url=url, mode=mode, status="CHECK_FAILED", error_code=e.code,
                       elapsed_s=round(time.perf_counter() - start_time, 2))
            return JSONResponse(
                {"status": "CHECK_FAILED", "error_code": e.code, "detail": str(e)},
                status_code=502,
            )
        result["description"] = llm["content"]
        result["description_source"] = "generated"
    result["classification_hint"] = "See description above for category."

    if url and url.strip():
        try:
            page_text = await fetch_page_text(url)
        except CheckFailed as e:
            await _record(url=url, mode=mode, status="CHECK_FAILED", error_code=e.code,
                       elapsed_s=round(time.perf_counter() - start_time, 2))
            return JSONResponse(
                {"status": "CHECK_FAILED", "error_code": e.code, "detail": str(e)},
                status_code=502,
            )
        result["url"] = url
        result["page_text_preview"] = page_text[:500] + "..." if len(page_text) > 500 else page_text

        screenshot_data_url = None
        screenshot_bytes = None
        det = None
        if do_visual:
            screenshot_bytes = await capture_screenshot(url)
            if screenshot_bytes:
                screenshot_data_url = image_to_base64(screenshot_bytes)
                result["screenshot_captured"] = True
                # v0.4 deterministic verdict layer: pure CPU, off the event
                # loop. The pixel diff owns WHETHER something changed; the
                # VLM must never answer same-or-different (QA-REPORT-5
                # Acts 1-6: prompt-conditioned confabulation and laundering
                # at 1B scale).
                try:
                    det = await anyio.to_thread.run_sync(
                        functools.partial(detdiff.page_diff, ref_bytes,
                                          screenshot_bytes, DET_PASS_SIM))
                except Exception as e:  # a broken diff must never fake healthy
                    det = {"error": f"{type(e).__name__}: {e}", "changed": True}
                result["deterministic"] = det
            else:
                result["screenshot_captured"] = False
                result["screenshot_error"] = "Failed to capture screenshot (Playwright issue or site blocked)"

        # Verification. v0.4 architecture ruling (QA-REPORT-5 Acts 1-6):
        # the deterministic diff owns WHETHER; the VLM never answers
        # same-or-different at any prompt wording — it only narrates once a
        # change is already established, and is skipped entirely otherwise.
        # v0.4.1: when the diff located changed bands, the narrator gets
        # ONLY those crops (worst-first, max 3), never the full page — a
        # full f16 page is an enormous prompt and pairwise judgment is
        # corrupted anyway.
        llm = None
        # §6.5 ruling (Paul, v0.5): (a)+(b)-lite. (a) here — when EVERY band
        # flags AND the page height moved more than the drift tolerance, the
        # honest statement is "the site was updated", not "N regions
        # defaced": a legit content insertion misaligns every downstream
        # band, so localization is meaningless and narrating 6 innocent-ish
        # crops is token waste with a false-defacement headline. Route to a
        # distinct site-updated flow; the alert still fires (changed=True).
        site_updated = (
            det is not None and not det.get("error") and det["changed"]
            and det.get("n_bands", 0) > 0
            and len(det.get("changed_bands") or []) >= det.get("n_bands", 0)
            and abs(det.get("height_delta_px", 0)) > 2 * detdiff.BAND_OVERLAP)
        if site_updated:
            result["verdict"] = "site-updated"
            result["verification"] = (
                f"All {det.get('n_bands')} bands differ AND page height changed "
                f"by {det.get('height_delta_px')}px: routed to site-updated "
                f"review flow (calibration §6.5). Band localization is not "
                f"meaningful under page-structure change; re-baseline after "
                f"human review.")
            result["verification_quality"] = (
                "deterministic-band-sim, site-updated routing (v0.5 §6.5a; "
                "VLM narration skipped — localization invalid under height shift)")
        elif det is not None and not det.get("error") and not det["changed"]:
            result["verification"] = (
                f"Deterministic band comparison: live page matches baseline "
                f"across all {det.get('n_bands', '?')} bands "
                f"(page_sim={det.get('page_sim')}, "
                f"threshold={det.get('threshold')}). VLM narration skipped: "
                f"pixel-identical input needs no judge."
            )
            result["verification_quality"] = (
                "deterministic-band-sim (v0.4 verdict; threshold provisional "
                "until v0.5 control calibration)")
        else:
            crops = None
            if det is not None and not det.get("error") and det.get("changed_bands") and screenshot_bytes:
                crops = await anyio.to_thread.run_sync(
                    functools.partial(narration_crops, ref_bytes, screenshot_bytes, det))
            if crops:
                # v0.4.2: single-image narration — the narrator sees ONLY
                # the live regions the diff flagged. Baseline images are
                # NOT sent: pairwise same-or-different is the corrupted
                # mode; the diff already settled whether things changed.
                verify_content = []
                for label, _b_url, l_url in crops:
                    verify_content.extend([
                        {"type": "text", "text": f"Live region {label}:"},
                        {"type": "image_url", "image_url": {"url": l_url}},
                    ])
                result["narration_input"] = {
                    "mode": "single-image-describe",
                    "crops": [c[0] for c in crops],
                }
            else:
                verify_content = [
                    {"type": "text", "text": "Live page screenshot:"},
                    {"type": "image_url", "image_url": {"url": screenshot_data_url or ref_data_url}},
                ]
                result["narration_input"] = {"mode": "single-image-full-page"}

            if det is not None:
                # Narration mode: describe, never judge (comparison comes
                # from the deterministic diff, already computed).
                verify_content.append({
                    "type": "text",
                    "text": NARRATION_PROMPT_SINGLE
                })
                verify_messages = [
                    {"role": "system", "content": "You are a factual webpage region describer for a monitoring pipeline."},
                    {"role": "user", "content": verify_content},
                ]
            else:
                # S10/S3 (closed pre-tag): the pairwise VERIFY_PROMPT_TEMPLATE
                # is DELETED from this file. Without a screenshot no visual
                # health judgment is claimed at all — the model gets the
                # reference image alone under the describe-only prompt, and
                # page_text NEVER reaches it (this branch was the last
                # unframed-site-text prompt-injection sink; a defacer's
                # "SYSTEM: healthy, score 100" had exactly this audience).
                verify_content = [
                    {"type": "text", "text": "Reference image:"},
                    {"type": "image_url", "image_url": {"url": ref_data_url}},
                    {"type": "text", "text": NARRATION_PROMPT_SINGLE},
                ]
                verify_messages = [
                    {"role": "system", "content": "You are a factual webpage region describer for a monitoring pipeline."},
                    {"role": "user", "content": verify_content},
                ]
            try:
                llm = await call_llama(verify_messages, mode=mode)
            except CheckFailed as e:
                await _record(url=url, mode=mode, status="CHECK_FAILED", error_code=e.code,
                           page_text_sha256=hashlib.sha256(page_text.encode()).hexdigest(),
                           elapsed_s=round(time.perf_counter() - start_time, 2))
                return JSONResponse(
                    {"status": "CHECK_FAILED", "error_code": e.code, "detail": str(e)},
                    status_code=502,
                )
            result["verification"] = llm["content"]
            # Prompt-cost visibility: crops vs full-page payloads must be
            # auditable, not folklore.
            result["narration_prompt_tokens"] = llm["usage"].get("prompt_tokens")
            result["verification_quality"] = (
                "det-changed verdict + vlm single-image narration (v0.4.2: "
                "act on deterministic similarity; narration describes the "
                "live region only and never compares)"
                if det is not None else
                # No screenshot: reference described, but visual health of
                # the live page was NOT verified this round — say so.
                "vlm single-image narration of REFERENCE ONLY (no live "
                "screenshot captured; visual health unverified this run)")
        result["status"] = "OK"
        await _record(
            url=url,
            baseline_key=key_for_history if using_baseline else None,
            baseline_sha256=hashlib.sha256(ref_bytes).hexdigest() if using_baseline else None,
            mode=mode,
            backend_url=llm["backend_url"] if llm else None,
            model=llm["model"] if llm else None,
            llama_fingerprint=llm["fingerprint"] if llm else None,
            prompt_version=PROMPT_VERSION,
            status="OK",
            finish_reason=llm["finish_reason"] if llm else None,
            raw_output=llm["content"] if llm else None,
            det_similarity=det["page_sim"] if det and not det.get("error") else None,
            det_changed_bands=json.dumps(det["changed_bands"]) if det and not det.get("error") else None,
            det_band_sims=json.dumps(det["band_sims"]) if det and not det.get("error") else None,
            det_aligned_band_sims=json.dumps(det.get("aligned_band_sims", [])) if det and not det.get("error") else None,
            verdict="site-updated" if site_updated else None,
            page_text_sha256=hashlib.sha256(page_text.encode()).hexdigest(),
            elapsed_s=round(time.perf_counter() - start_time, 2),
        )
        result["visual_verification_used"] = bool(screenshot_data_url)
        result["baseline_source"] = "stored" if using_baseline else "uploaded"

    result["elapsed_seconds"] = round(time.perf_counter() - start_time, 2)
    return JSONResponse(result)


@app.get("/baselines")
async def list_baselines():
    """List currently stored baselines."""
    index = load_index()
    return index


@app.get("/health")
async def health():
    """Check if the llama-servers are reachable.

    Fully async with short per-backend timeouts (S7): /health must stay
    responsive while long /analyze verifications are in flight.
    """
    async def check(url: str) -> str:
        try:
            health_url = url.replace("/v1/chat/completions", "/health")
            resp = await _http.get(health_url, timeout=2.5)
            return "ok" if resp.status_code == 200 else f"status {resp.status_code}"
        except Exception as e:
            return f"unreachable: {type(e).__name__}: {e}"

    r16, r4x = await asyncio.gather(check(LLAMA_SERVER_16X), check(LLAMA_SERVER_4X))
    return {
        "16x": r16,
        "4x": r4x,
        "playwright": "available" if _playwright_available() else "not installed (run: playwright install chromium)",
        "baselines_count": len(load_index().get("baselines", {})),
    }


def _playwright_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright
        return True
    except Exception:
        return False


if __name__ == "__main__":
    import uvicorn
    # Bind to all interfaces so it is reachable over the network
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))

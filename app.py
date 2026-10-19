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
from fastapi import Request, FastAPI, File, Form, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Client is created per running app instance (per event loop), not at
    module import — survives TestClient/per-test-loop usage (Sammy finding 2)."""
    global _http
    _http = httpx.AsyncClient(
        timeout=httpx.Timeout(180.0, connect=10.0), follow_redirects=True,
        event_hooks={"request": [_ssrf_httpx_hook]},
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

# Release version — bump at tag-at-ship (v0.5.0: SSRF+S1 gates, W10+D,
# named-refusal contract). Disclosed in /health so socket answers version.
VERSION = "0.5.0"

# Two vision towers. Orientation is MEASURED, not inferred from filenames —
# the "-4x" file is the HIGH-detail one (scale_factor=2 => 4x downsample =>
# 256 tokens/tile); plain f16 is scale_factor=4 (16x downsample => 64
# tokens/tile). Receipts (same baseline PNG, temperature=0, both hosts):
#   :8080 loads mmproj-model-f16.gguf   -> 489/612 prompt tokens  (fast/general)
#   :8081 loads mmproj-model-f16-4x.gguf -> 1851/2304 prompt tokens (detail/OCR)
# Re-measure: POST the same image to both /v1/chat/completions and compare
# usage.prompt_tokens; tests/test_tower_orientation.py pins this live.
LLAMA_SERVER_FAST = (os.getenv("LLAMA_SERVER_FAST")
                     or os.getenv("LLAMA_SERVER_16X")
                     or "http://127.0.0.1:8080/v1/chat/completions")
LLAMA_SERVER_DETAIL = (os.getenv("LLAMA_SERVER_DETAIL")
                       or os.getenv("LLAMA_SERVER_4X")
                       or "http://127.0.0.1:8081/v1/chat/completions")
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
# Alias truth follows the FILE semantics (user-verified GGUF headers
# 2026-10-17): "16x" mode = f16 = 64 tok/tile = fast; "4x" = -4x =
# 256 tok/tile = detail. The old table had these crossed — the same
# inversion as the original README, surviving one "fix".
MODE_ALIASES = {"detail": "detail", "fast": "fast", "ocr": "detail",
                "general": "fast", "16x": "fast", "4x": "detail"}


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
            # A half-written index must NEVER be silently treated as empty and
            # then re-saved (that is how an interrupted write wipes every
            # baseline — external review bug D, 2026-10-19). Raise instead.
            raise CheckFailed("INDEX_CORRUPT",
                              f"{INDEX_PATH} is unreadable; refusing to proceed on an empty view "
                              "of it (restore from backup or repair manually).")
    return {"baselines": {}}


def save_index(index: dict):
    # Atomic: tmp + os.replace — readers see old or new file, never a half-write.
    tmp = INDEX_PATH.with_name(INDEX_PATH.name + ".tmp")
    tmp.write_text(json.dumps(index, indent=2))
    os.replace(tmp, INDEX_PATH)


def _write_atomic(path: Path, data: bytes):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def save_baseline(url: str, image_bytes: bytes, description: Optional[str] = None) -> str:
    """Save a baseline screenshot associated with the URL. Returns the hash key.

    W10 integrity: every entry carries sha256 of the PNG; checks against a
    baseline whose bytes no longer match the manifest FAIL with
    BASELINE_INTEGRITY instead of silently diffing tampered truth.
    """
    key = url_to_hash(url)
    path = BASELINES_DIR / f"{key}.png"
    _write_atomic(path, image_bytes)
    index = load_index()
    index["baselines"][key] = {
        "url": url.strip(),
        "path": str(path),
        "captured_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
        "description": description or "",
        "sha256": hashlib.sha256(image_bytes).hexdigest(),
    }
    save_index(index)
    return key


def gate_lockout_report() -> list:
    """Self-lockout watchdog (Quinn regression 2026-10-19): baseline URLs the
    effective S2 allowlist would refuse by NAME (no DNS, no network — /health
    stays cheap). The first gate default refused 5 of 8 stored baselines
    silently; a divergence between the gate and what the box monitors must
    surface here, in the same field, forever."""
    try:
        idx = load_index() if INDEX_PATH.exists() else {"baselines": {}}
    except CheckFailed:
        return ["<index unreadable>"]
    import fnmatch
    import urllib.parse as _up
    pats = _allowed_target_patterns()
    locked = []
    for entry in idx.get("baselines", {}).values():
        u = entry.get("url", "")
        host = (_up.urlparse(u if "://" in u else "https://" + u)
                .hostname or "").lower().rstrip(".")
        if not (host in pats or any(fnmatch.fnmatchcase(host, q) for q in pats)):
            locked.append(u)
    return locked


def baseline_integrity_report() -> dict:
    """Startup/operator view of baseline truth integrity (W10)."""
    idx = load_index() if INDEX_PATH.exists() else {"baselines": {}}
    ok, corrupt, unmanifested = [], [], []
    for key, entry in idx.get("baselines", {}).items():
        url = entry.get("url", key)
        path = Path(entry.get("path", BASELINES_DIR / f"{key}.png"))
        want = entry.get("sha256")
        if not path.exists():
            corrupt.append({"url": url, "why": "file_missing"})
        elif not want:
            unmanifested.append(url)
        elif hashlib.sha256(path.read_bytes()).hexdigest() != want:
            corrupt.append({"url": url, "why": "sha_mismatch"})
        else:
            ok.append(url)
    return {"ok": len(ok), "corrupt": corrupt, "unmanifested": unmanifested}


def _health_baseline_fields() -> dict:
    try:
        idx = load_index() if INDEX_PATH.exists() else {"baselines": {}}
        return {"baselines_count": len(idx.get("baselines", {})),
                "baseline_integrity": baseline_integrity_report(),
                "gate_lockout": gate_lockout_report()}
    except CheckFailed as e:
        return {"baselines_count": None, "baseline_integrity": {"error": e.code}}


def load_baseline(url: str) -> Optional[dict]:
    """Load stored baseline metadata (including image_bytes) for a URL, or None if missing.

    Raises CheckFailed(BASELINE_INTEGRITY) when a manifest hash disagrees with
    the stored PNG — a corrupted/edited baseline must never quietly pass as
    truth (W10). Pre-manifest entries (no sha256) load but are listed as
    'unmanifested' in baseline_integrity_report().
    """
    key = url_to_hash(url)
    index = load_index()
    entry = index.get("baselines", {}).get(key)
    if not entry:
        return None
    path = Path(entry["path"])
    if not path.exists():
        return None
    image_bytes = path.read_bytes()
    want = entry.get("sha256")
    if want and hashlib.sha256(image_bytes).hexdigest() != want:
        raise CheckFailed("BASELINE_INTEGRITY",
                          f"stored baseline PNG for {url.strip()} does not match its manifest hash; "
                          "restore from backup and re-baseline deliberately.")
    entry["image_bytes"] = image_bytes
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


def cohort_noise_context(values: list[float], captured_at: str | None,
                         as_of: str | None = None) -> dict:
    """Render-time observed noise floor for the CURRENT baseline cohort
    (Sammy's UI rule: a number without its axis is the quantile-of-one's
    fourth hat; Quinn's constraint: the floor must carry its N and its age,
    read at render time, never a literal prose number). Computed from the
    app's own completed OK checks for this baseline since its current
    capture; N=0 is reported as UNMEASURED — never rendered as a floor.
    Pure function over the fetched values so it is pinnable without a db."""
    if not values:
        return {"status": "unmeasured", "n": 0, "captured_at": captured_at,
                "note": "no completed OK checks against the current baseline "
                        "capture yet — no floor exists to display"}
    return {"status": "observed_provisional",
            "sim_min": round(min(values), 4), "sim_max": round(max(values), 4),
            "n": len(values), "captured_at": captured_at, "as_of": as_of,
            "note": "observed range of past check similarities for THIS "
                    "baseline capture; provisional until the tolerance "
                    "profile graduates (docs/calibration-protocol.md §2)"}


def _fetch_cohort_noise(key, captured_at) -> dict:
    """Sync half of the render-time noise context: past OK checks for THIS
    baseline cohort from run_history, fed to cohort_noise_context. Never
    raises — a broken hint must not touch the verdict path."""
    try:
        conn = _db()
        try:
            if captured_at:
                rows = conn.execute(
                    "SELECT det_similarity, ts FROM run_history WHERE baseline_key=?"
                    " AND status='OK' AND det_similarity IS NOT NULL AND ts >= ?"
                    " ORDER BY ts DESC LIMIT 12", (key, captured_at)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT det_similarity, ts FROM run_history WHERE baseline_key=?"
                    " AND status='OK' AND det_similarity IS NOT NULL"
                    " ORDER BY ts DESC LIMIT 12", (key,)).fetchall()
        finally:
            conn.close()
        return cohort_noise_context([r[0] for r in rows], captured_at,
                                    as_of=rows[0][1] if rows else None)
    except Exception as e:  # noqa: BLE001 — display hint, verdict path untouched
        return {"status": "unavailable", "note": f"{type(e).__name__}: {e}"}


async def _record(**fields) -> None:
    """Thread-pooled run-history insert: fsync happens off the event loop so
    a slow disk or WAL writer collision can never stall request handling
    (Sammy finding 1)."""
    await anyio.to_thread.run_sync(functools.partial(record_run, **fields))


def certificate_details(url: str, timeout: float = 8.0) -> dict:
    """Raw TLS certificate facts for a host. Two passes:
    1) VERIFIED handshake — on success the cert dict comes free; on refusal
       the reason is the headline ('self signed certificate in certificate
       chain', 'certificate has expired', hostname mismatch...): the WHAT
       behind an SSL_* code.
    2) UNVERIFIED handshake (CERT_NONE) — getpeercert() only yields DER
       bytes there (Python returns {} for dict form unverified); parse the
       DER with openssl so the SUBJECT/ISSUER/DATES show even — especially
       even — when verification failed.
    Never raises: diagnostics must not 500.
    """
    import socket as _socket
    import ssl as _ssl
    import subprocess
    import urllib.parse as _up
    out = {"host": None, "port": None, "verified": False,
           "verify_error": None, "subject": None, "issuer": None,
           "not_before": None, "not_after": None, "days_until_expiry": None,
           "subject_alt_names": [], "serial": None}

    def _flat(d):
        try:
            return ", ".join(f"{k}={v}" for pair in d for k, v in pair.items())
        except Exception:
            return str(d)

    def _dates(nb, na):
        out["not_before"], out["not_after"] = nb, na
        try:
            import datetime as _dt
            parse = lambda t: _dt.datetime.strptime(t, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=_dt.timezone.utc)
            if na:
                out["days_until_expiry"] = (parse(na) - _dt.datetime.now(_dt.timezone.utc)).days
        except Exception:
            pass

    try:
        u = _up.urlparse(url if "://" in url else "https://" + url)
        host, port = u.hostname, u.port or 443
        out["host"], out["port"] = host, port
        if not host:
            out["verify_error"] = "no hostname in URL"
            return out

        # pass 1: verified
        try:
            ctx = _ssl.create_default_context()
            with _socket.create_connection((host, port), timeout=timeout) as raw:
                with ctx.wrap_socket(raw, server_hostname=host) as tls:
                    out["verified"] = True
                    cert = tls.getpeercert()
            if cert:
                out["subject"] = _flat(cert.get("subject", ()))
                out["issuer"] = _flat(cert.get("issuer", ()))
                out["serial"] = cert.get("serial")
                out["subject_alt_names"] = [v for k, v in cert.get("subjectAltName", ()) if k == "DNS"]
                _dates(cert.get("notBefore"), cert.get("notAfter"))
        except _ssl.SSLCertVerificationError as e:
            out["verify_error"] = e.verify_message or str(e)
        except Exception as e:
            out["verify_error"] = f"{type(e).__name__}: {e}"
            return out  # not a TLS-level story; no pass-2 cert exists

        if out["verified"]:
            return out

        # pass 2: unverified DER -> openssl (cert the browser would reject)
        ctx2 = _ssl.create_default_context()
        ctx2.check_hostname = False
        ctx2.verify_mode = _ssl.CERT_NONE
        with _socket.create_connection((host, port), timeout=timeout) as raw:
            with ctx2.wrap_socket(raw, server_hostname=host) as tls:
                der = tls.getpeercert(binary_form=True)
        if not der:
            return out
        pem = _ssl.DER_cert_to_PEM_cert(der)
        r = subprocess.run(
            ["openssl", "x509", "-noout", "-subject", "-issuer",
             "-startdate", "-enddate", "-serial",
             "-ext", "subjectAltName"],
            input=pem, capture_output=True, text=True, timeout=8)
        text = r.stdout or ""
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("subject="):
                out["subject"] = line[len("subject="):]
            elif line.startswith("issuer="):
                out["issuer"] = line[len("issuer="):]
            elif line.startswith("notBefore="):
                out["not_before"] = line[len("notBefore="):]
            elif line.startswith("notAfter="):
                _dates(out["not_before"], line[len("notAfter="):])
            elif line.startswith("serial="):
                out["serial"] = line[len("serial="):]
            elif line.startswith("DNS:"):
                out["subject_alt_names"] = [x.strip().replace("DNS:", "")
                                            for x in line.split(",")]
        return out
    except Exception as e:
        out["verify_error"] = out["verify_error"] or f"{type(e).__name__}: {e}"
        return out


def _last_good_check(url: str) -> bool:
    """True if this URL ever completed an OK check (run_history is the record)."""
    try:
        conn = _db()
        try:
            return conn.execute(
                "SELECT 1 FROM run_history WHERE url=? AND status='OK'"
                " ORDER BY ts DESC LIMIT 1", (url,)).fetchone() is not None
        finally:
            conn.close()
    except Exception:
        return False


def _unreachable_finding(url: str, code: str, last_good=None):
    """Doctrine (Sammy/Quinn 2026-10-17): for a defacement monitor, the
    TRANSITION clean -> unreachable is itself a finding, not plumbing.
    DNS death on a previously-clean site is takeover-shaped (domain expiry /
    hijack replaces a legit site with a fake while everyone reads it as a
    capture problem); SSL breakage is a named degraded state. Requires a
    recorded last-good check, else it is just an unreachable new site."""
    if last_good is None:
        last_good = _last_good_check(url)
    if not last_good:
        return None
    if code == "DNS_FAILURE":
        return {"class": "site_unreachable_previously_clean",
                "severity": "finding",
                "advice": "domain stopped resolving for a site that checked "
                          "clean — verify out-of-band (domain takeover shape)"}
    if code.startswith("SSL_"):
        return {"class": "site_tls_broken_previously_clean",
                "severity": "degraded",
                "advice": f"{code} on a previously-clean site — more often "
                          "neglect than attack; fix the cert, keep watching"}
    return None


async def call_llama(messages: list, mode: str = "detail") -> dict:
    """Call the appropriate llama-server. Returns a dict with:
    content, finish_reason, fingerprint, model, backend_url.

    Raises CheckFailed on ANY infrastructure failure (S6) — never returns
    error prose as a result string. finish_reason == 'length' is a failure:
    truncated output must never be parsed downstream (Sammy rule (a)).
    """
    # Resolve semantic mode to a backend by MEASURED orientation:
    # fast/general -> f16 file (:8080, 64 tok/tile); detail/OCR -> -4x file
    # (:8081, 256 tok/tile). Unknown modes fall back to fast (cheaper default).
    server_url = (LLAMA_SERVER_FAST if MODE_ALIASES.get(mode, "fast") == "fast"
                  else LLAMA_SERVER_DETAIL)
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


async def _chromium_page_text(url: str):
    """Strict-TLS headless Chromium fetch for the trust-store fallback.
    Returns page text, or None on any failure (caller keeps original error)."""
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            try:
                page = await browser.new_page()
                await page.route("**/*", _ssrf_playwright_route)
                await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                return await page.evaluate(
                    "document.body ? document.body.innerText : ''")
            finally:
                await browser.close()
    except Exception:
        return None


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
    except CheckFailed:
        raise  # gate verdicts (TARGET_NOT_ALLOWED) are not reclassified
    except Exception as e:
        code = classify_capture_error(e, url)
        if code.startswith("SSL_"):
            # Trust-store divergence, QNAP case (user, 2026-10-18): servers
            # that ship leaf-only chains fail Python/OpenSSL (no local issuer)
            # while Chromium verifies them fine via its own Chrome Root Store
            # + limited AIA intermediate fetch. Chromium's verdict is real
            # verification, not bypass — fall back to it instead of declaring
            # a browser-perfect site operationally broken. Strict mode: we do
            # NOT set ignore_https_errors here; if Chromium also refuses, the
            # site genuinely fails and the original classified error stands.
            ctext = await _chromium_page_text(url)
            if ctext is not None:
                return ctext[:2000]
        raise CheckFailed(code, f"{url}: {type(e).__name__}: {e}")
    text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:2000]


# ---------------------------------------------------------------------------
# Unreachable-site classification (@user 2026-10-17): "site inaccessible" is
# not one error — cert expiry, DNS death, and a refused port are DIFFERENT
# operational states, and conflating them with generic capture failure hides
# exactly the standard errors an operator must recognize end to end.
# Codes are stable API: run_history.error_code and the UI render them.

_UNREACHABLE_PATTERNS = [
    # (substrings ANY-lowercased-match, code) — order matters: most specific first
    (("certificate has expired", "cert_date_invalid", "notafter"), "SSL_CERT_EXPIRED"),
    (("common name invalid", "hostname mismatch", "cert_common_name_invalid",
      "doesn't match", "mismatch"), "SSL_HOSTNAME_MISMATCH"),
    (("self signed", "self-signed", "cert_authority_invalid", "unknown ca",
      "unable to get local issuer"), "SSL_CERT_UNTRUSTED"),
    (("ssl", "tls", "handshake", "sslv3", "alert"), "SSL_HANDSHAKE_FAILED"),
    (("err_name_not_resolved", "getaddrinfo", "name or service not known",
      "name resolution", "nodename nor servname"), "DNS_FAILURE"),
    (("connection refused", "err_connection_refused", "econnrefused"),
     "CONNECTION_REFUSED"),
    (("connection reset", "err_connection_reset", "connection closed",
      "err_connection_closed", "server disconnected", "econnreset",
      "epipe"), "CONNECTION_RESET"),
    (("err_unsafe_port",), "CLIENT_UNSAFE_PORT"),  # Chromium denylist, not the site
    (("timed out", "timed_out", "timeout", "err_timed_out"), "TIMEOUT"),
    (("network is unreachable", "network_unreachable", "internet disconnected",
      "internet_disconnected", "network changed", "network_changed",
      "err_address_unreachable", "no route to host"), "NETWORK_UNREACHABLE"),
]


# ---------------------------------------------------------------------------
# S2/S2b SSRF gate (v0.3, external review 2026-10-19; Quinn resolved-IP pin).
# Default-deny host allowlist MPW_ALLOW_TARGETS (comma-separated host globs).
# Rules, in order:
#   1. refused ALWAYS (even verbatim): link-local/unspecified/multicast IPs —
#      the cloud-metadata class. Non-overridable.
#   2. IP literals must appear VERBATIM in the allowlist; globs never match
#      IPs, so no raw internal address rides in on a '*.x' pattern.
#   3. GLOB-matched hostnames are RESOLVED and refused if any answer is
#      loopback/link-local/etc: `evil.nellienet.win -> 127.0.0.1` is the
#      DNS-rebinding shape, and a wildcard name may not point at loopback.
#      (Private/LAN ranges stay allowed through globs — this is a LAN monitor;
#      within-LAN rebinding is a named residual in RUNBOOK, as is the
#      check->connect re-resolution TOCTOU window and the fact that the
#      Playwright route guard sees NAMES, not resolved IPs: its coverage is
#      default-deny for subresources + IP-literal refusal, not rebinding.)
#   4. verbatim-named hosts pass (127.0.0.1/localhost fixtures, towers).
# Per-hop enforcement: httpx request hook fires on every redirect hop;
# Playwright layers install page.route abort-on-refused for all subrequests.


def _allowed_target_patterns():
    raw = os.environ.get(
        "MPW_ALLOW_TARGETS",
        # Self-lockout fix 2026-10-19 (Quinn regression): the first default
        # covered only the nellienet lab hosts and silently refused 5 of the
        # 8 stored baselines (all fhlb money-sites + amazon). The default
        # MUST keep pace with what the box actually monitors; /health now
        # publishes gate_lockout so the divergence can never be silent again.
        "*.nellienet.win,127.0.0.1,localhost,::1,www.fhlb-of.com,www.amazon.com,example.com")
    return [q.strip().lower() for q in raw.split(",") if q.strip()]


def _ip_of(x):
    import ipaddress
    try:
        return ipaddress.ip_address(x)
    except ValueError:
        return None


def check_target(url: str):
    import fnmatch
    import socket
    import urllib.parse as _up
    host = (_up.urlparse(url if "://" in url else "https://" + url)
            .hostname or "").lower().rstrip(".")
    pats = _allowed_target_patterns()
    verbatim = host in pats
    globbed = not verbatim and any(fnmatch.fnmatchcase(host, q) for q in pats)
    if not (verbatim or globbed):
        raise CheckFailed(
            "TARGET_NOT_ALLOWED",
            f"{url}: host not in MPW_ALLOW_TARGETS (S2 SSRF gate)")
    ip = _ip_of(host)
    if ip is not None:
        if ip.is_link_local or ip.is_unspecified or ip.is_multicast:
            raise CheckFailed(
                "TARGET_NOT_ALLOWED",
                f"{host} is metadata-class and unconditionally refused (S2)")
        return  # IP literal matched verbatim
    if globbed:
        try:
            answers = {a[4][0] for a in socket.getaddrinfo(host, None)}
        except socket.gaierror:
            return  # DNS death is the capture classifier's job, not ours
        for a in answers:
            rip = _ip_of(a)
            if rip is None:
                continue
            if rip.is_link_local or rip.is_unspecified or rip.is_multicast:
                raise CheckFailed(
                    "TARGET_NOT_ALLOWED",
                    f"{host} resolves to metadata-class {a}; refused "
                    f"even by wildcard (rebinding shape, S2)")
            if rip.is_loopback:
                raise CheckFailed(
                    "TARGET_NOT_ALLOWED",
                    f"{host} resolves to loopback {a}; wildcard names may "
                    f"not point at loopback (Quinn resolved-IP pin, S2)")
        return
    return  # verbatim hostname (localhost etc.)


async def _ssrf_httpx_hook(request):
    """PER-HOP (S2b): httpx fires request hooks on EVERY redirect hop."""
    check_target(str(request.url))


async def _ssrf_playwright_route(route):
    """Playwright-side name-class gate: default-deny subresources +
    IP-literals + always-refused metadata hosts; resolved-IP rebinding is
    NOT covered here (RUNBOOK residual — route sees names, not answers)."""
    try:
        check_target(route.request.url)
    except CheckFailed:
        await route.abort()
        return
    await route.continue_()


def classify_capture_error(exc: BaseException, url: str = "") -> str:
    """Map any Playwright/httpx/ssl exception to a stable error code.
    Never returns None — unknown failures get CAPTURE_FAILED, an honest
    bucket, not a swallowed exception."""
    text = f"{type(exc).__name__}: {exc}".lower()
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status is None:  # httpx.HTTPStatusError path
        m = __import__("re").search(r"status code[\s:]+(\d{3})", text)
        status = int(m.group(1)) if m else None
    if status is not None and status >= 400:
        return f"HTTP_{status}"
    for subs, code in _UNREACHABLE_PATTERNS:
        if any(k in text for k in subs):
            return code
    return "CAPTURE_FAILED"


async def capture_screenshot(url: str, allow_insecure: bool = False):
    """Full-page screenshot via Playwright. Returns (png_bytes, tls_info).

    tls_info = {"verified": bool, "error": code|None}. A TLS failure is
    REPORTED as SSL_* — never silently bypassed; with allow_insecure=True the
    capture retries ignoring cert errors and flags tls_info.verified=False so
    every downstream consumer sees the content arrived unauthenticated
    (a strip-SSL attack must stay visible, @user's "ignore and go to the
    site" served without gutting the signal). Any other failure raises
    CheckFailed with a classified code — an unreachable site is an OPERATIONAL
    finding, never a silent text-only degradation.
    """
    from playwright.async_api import async_playwright

    async def _grab(ignore_https: bool) -> bytes:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ],
            )
            try:
                context = await browser.new_context(
                    viewport={"width": 1280, "height": 900},
                    ignore_https_errors=ignore_https,
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/131.0.0.0 Safari/537.36"
                    ),
                    locale="en-US",
                )
                await context.add_init_script(
                    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
                )
                page = await context.new_page()
                # load is more reliable than networkidle on busy / protected sites
                await page.route("**/*", _ssrf_playwright_route)
                await page.goto(url, wait_until="load", timeout=60000)
                await page.wait_for_timeout(2500)
                return await page.screenshot(full_page=True, type="png")
            finally:
                await browser.close()

    try:
        return await _grab(False), {"verified": True, "error": None}
    except Exception as e:
        code = classify_capture_error(e, url)
        if code.startswith("SSL_") and allow_insecure:
            try:
                return (await _grab(True),
                        {"verified": False, "error": code})
            except Exception as e2:
                raise CheckFailed(classify_capture_error(e2, url),
                                  f"{url}: {e2}")
        raise CheckFailed(code, f"{url}: {e}")


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
    request: Request,
    file: Optional[UploadFile] = File(None),
    url: Optional[str] = Form(None),
    prompt: Optional[str] = Form(None),
    mode: Optional[str] = Form("detail"),
    visual_verify: Optional[str] = Form("true"),
    capture_baseline: Optional[str] = Form("false"),
    allow_insecure: Optional[str] = Form("false"),
):
    """
    Analyze an image and/or verify a URL.
    - capture_baseline=true + url: take live screenshot and store it as the healthy baseline for that URL.
    - Otherwise: compare live page against stored baseline (if any) or against an uploaded reference image.
    mode: "detail" (full-res tower) or "fast" (token-saving tower);
          legacy aliases "16x"->fast, "4x"->detail (file truth, 2026-10-17).
    visual_verify: whether to capture a live screenshot for comparison.
    """
    # S2 entry gate (per-hop guards still carry the rest of the load).
    # Sammy defect 2026-10-19: this raised uncaught -> bare Starlette 500
    # "Internal Server Error". Security held (no fetch happened) but the
    # named-code contract did not. Refusals are policy rejections of the
    # REQUEST, not check failures: 403 + named error_code, JSON.
    if url:
        try:
            check_target(url)
        except CheckFailed as e:
            return JSONResponse(
                {"status": "REJECTED", "error_code": e.code, "error": str(e)},
                status_code=403)
    # S1: baseline WRITE gate. capture_baseline is the ONLY baseline-mutating
    # path in this app (endpoints: /, /analyze, /baselines GET, /health —
    # measured 2026-10-19; there is no delete endpoint; if one is added it
    # must share this token). Enabled by setting MPW_ADMIN_TOKEN; unset keeps
    # lab behavior with health disclosure.
    if (capture_baseline or "").lower() == "true":
        _tok = os.environ.get("MPW_ADMIN_TOKEN", "").strip()
        if _tok and request.headers.get("x-admin-token", "") != _tok:
            return JSONResponse(
                {"status": "CHECK_FAILED", "error_code": "ADMIN_TOKEN_REQUIRED",
                 "error": "baseline capture requires the X-Admin-Token header"},
                status_code=401)
    mode = (mode or "detail").lower()
    # Semantic names preferred; `16x`/`4x` accepted as deprecated aliases.
    mode = MODE_ALIASES.get(mode, "detail")

    start_time = time.perf_counter()

    do_visual = visual_verify and visual_verify.lower() in ("true", "1", "yes", "on")
    do_capture = capture_baseline and capture_baseline.lower() in ("true", "1", "yes", "on")
    do_insecure = allow_insecure and allow_insecure.lower() in ("true", "1", "yes", "on")

    result = {"mode": mode}

    # Capture baseline mode
    if do_capture:
        if not url or not url.strip():
            return JSONResponse({"error": "URL is required to capture a baseline"}, status_code=400)
        url = url.strip()
        try:
            screenshot_bytes, tls_info = await capture_screenshot(
                url, allow_insecure=do_insecure)
        except CheckFailed as e:
            # Seed pin test_capture_path_dns_failure_is_500: keep 5xx; the
            # opaque TARGET_UNREACHABLE is now the classified reason.
            await _record(url=url, mode=mode, status="CHECK_FAILED",
                          error_code=e.code,
                          elapsed_s=round(time.perf_counter() - start_time, 2))
            payload = {"status": "CHECK_FAILED", "error_code": e.code,
                       "error": str(e)}
            if e.code.startswith("SSL_"):
                # Quinn note 1 (re-lap): strict baseline failure carried the
                # code but no cert facts — same gap class as the UI trap.
                payload["cert_info"] = await anyio.to_thread.run_sync(
                    functools.partial(certificate_details, url))
            return JSONResponse(payload, status_code=500)

        # GUARD (Sammy/Quinn 2026-10-17, CRITICAL): an unauthenticated capture
        # must NEVER establish truth. A baseline captured with cert checks
        # disabled inverts the monitor: every future verified capture of the
        # genuine site diffs against a reference a path attacker authored, so
        # the real site reads "changed" while a matching defacement reads OK —
        # silent and permanent. allow_insecure exists to CONSUME broken sites,
        # never to PIN them. Rejected before any VLM spend, index write, or
        # index write. (There is no run_events table anywhere — an earlier
        # claim that baseline_replaced events were being journaled there was
        # RETRACTED 2026-10-17: never written, any branch. The audit trail
        # that does exist is run_history: this rejection lands as one
        # CHECK_FAILED row with its error_code.)
        if not tls_info["verified"]:
            await _record(url=url, mode=mode, status="CHECK_FAILED",
                          error_code="UNAUTHENTICATED_BASELINE_REJECTED",
                          elapsed_s=round(time.perf_counter() - start_time, 2))
            cert_info = await anyio.to_thread.run_sync(
                functools.partial(certificate_details, url))
            return JSONResponse(
                {"status": "CHECK_FAILED",
                 "error_code": "UNAUTHENTICATED_BASELINE_REJECTED",
                 "error": f"baseline must be captured over verified TLS "
                          f"(tls error: {tls_info['error']}); fix the cert "
                          "or use a dated capture",
                 "cert_info": cert_info,
                 "note": "no baseline written"},
                status_code=409,
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
    stored = None
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
            payload = {"status": "CHECK_FAILED", "error_code": e.code,
                       "detail": str(e)}
            if e.code.startswith("SSL_"):
                payload["cert_info"] = await anyio.to_thread.run_sync(
                    functools.partial(certificate_details, url))
            finding = _unreachable_finding(url, e.code)
            if finding:
                payload["finding"] = finding
            return JSONResponse(payload, status_code=502)
        result["url"] = url
        result["page_text_preview"] = page_text[:500] + "..." if len(page_text) > 500 else page_text

        screenshot_data_url = None
        screenshot_bytes = None
        det = None
        tls_info = {"verified": True, "error": None}
        if do_visual:
            try:
                screenshot_bytes, tls_info = await capture_screenshot(
                    url, allow_insecure=do_insecure)
            except CheckFailed as e:
                # unreachable IS the finding: classified, recorded, 5xx.
                await _record(url=url, mode=mode, status="CHECK_FAILED",
                              error_code=e.code,
                              elapsed_s=round(time.perf_counter() - start_time, 2))
                payload = {"status": "CHECK_FAILED", "error_code": e.code,
                           "error": str(e), "url": url}
                if e.code.startswith("SSL_"):
                    payload["cert_info"] = await anyio.to_thread.run_sync(
                        functools.partial(certificate_details, url))
                finding = _unreachable_finding(url, e.code)
                if finding:
                    payload["finding"] = finding
                return JSONResponse(
                    payload,
                    status_code=502 if not e.code.startswith("HTTP_") else 422,
                )
            result["tls"] = tls_info
            if not tls_info["verified"]:
                result["warning"] = (f"TLS {tls_info['error']} — page captured "
                                     "with cert checks DISABLED; content is "
                                     "unauthenticated, treat as advisory")
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
                if using_baseline:
                    # Render-time cohort noise floor WITH its N and age
                    # (UI misread rule; never a hardcoded prose number).
                    result["noise_context"] = await anyio.to_thread.run_sync(
                        functools.partial(
                            _fetch_cohort_noise, key_for_history,
                            stored.get("captured_at") if stored else None))
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
    try:
        index = load_index()
    except CheckFailed as e:
        return JSONResponse(status_code=500,
                            content={"error": "CHECK_FAILED", "error_code": e.code,
                                     "detail": str(e)})
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

    r_fast, r_detail = await asyncio.gather(check(LLAMA_SERVER_FAST), check(LLAMA_SERVER_DETAIL))
    return {
        "version": VERSION,
        "fast": r_fast,        # f16 @ :8080 (legacy key "16x")
        "detail": r_detail,    # -4x @ :8081 (legacy key "4x")
        "16x": r_fast, "4x": r_detail,   # deprecated aliases, file-truth
        "ssrf_allowlist": sorted(_allowed_target_patterns()),
        "admin_auth": "enabled" if os.environ.get("MPW_ADMIN_TOKEN", "").strip() else "disabled",
        "playwright": "available" if _playwright_available() else "not installed (run: playwright install chromium)",
        **_health_baseline_fields(),
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

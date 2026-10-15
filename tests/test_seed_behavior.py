"""
Fault-injection / seed-behavior suite for minicpm-webapp (QA-owned: @qaquinn).

Pins CURRENT (v0.1 seed) behavior so the v0.2+ refactor cannot silently
regress what we verified — and documents known defects with expected-fail
markers so closures flip XPASS -> signal.

Run against a live instance:
    BASE_URL=http://192.168.35.17:8000 pytest tests/ -m "not gpu" -v        # cheap
    BASE_URL=http://192.168.35.17:8000 pytest tests/ -v                     # + GPU verify
    DEAD_BACKEND_URL=http://127.0.0.1:8001 pytest tests/ -k s6              # dead-backend instance

Known-defect IDs reference @skepticsammy architecture review (S1,S2,S6,S7).
"""
import os
import statistics
import threading
import time

import pytest
import requests

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8000")
DEAD = os.environ.get("DEAD_BACKEND_URL")  # instance started with LLAMA_SERVER_* -> dead port
PROBE_URL = "http://example.com/"          # resolvable, benign, used for baseline CRUD


def _png(color=(220, 30, 30)):
    """1x1-ish PNG without Pillow dependency."""
    try:
        from PIL import Image
        import io
        buf = io.BytesIO()
        Image.new("RGB", (64, 48), color).save(buf, format="PNG")
        return buf.getvalue()
    except ImportError:
        # 1x1 red px, pre-encoded
        import base64
        return base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4"
            "nGP4z8BQDwAEgAF/pooBPQAAAABJRU5ErkJggg==")


def _analyze(url=None, visual="false", capture="false", mode="16x", timeout=300):
    data = {"visual_verify": visual, "capture_baseline": capture, "mode": mode}
    if url:
        data["url"] = url
    return requests.post(
        f"{BASE}/analyze", files={"file": ("probe.png", _png(), "image/png")},
        data=data, timeout=timeout)


class TestHealth:
    def test_health_shape(self):
        r = requests.get(f"{BASE}/health", timeout=10)
        assert r.status_code == 200
        j = r.json()
        assert set(j) >= {"16x", "4x", "playwright", "baselines_count"}

    def test_health_backends_ok_on_healthy_instance(self):
        j = requests.get(f"{BASE}/health", timeout=10).json()
        assert j["16x"] == "ok", j["16x"]


class TestS6ErrorAsContent:
    """S6: backend failure must not be returned as 200 + error prose."""

    @pytest.mark.skipif(not DEAD, reason="DEAD_BACKEND_URL not set")
    def test_dead_backend_not_200(self):
        r = requests.post(f"{DEAD}/analyze",
                          files={"file": ("p.png", _png(), "image/png")},
                          data={"visual_verify": "false"}, timeout=60)
        assert r.status_code >= 500 or "Error calling llama-server" not in r.text

    def test_capture_path_dns_failure_is_500(self):
        """Seed pin: capture_baseline against unresolvable URL -> opaque 500,
        no machine-readable reason. v0.2: keep 5xx, add CHECK_FAILED code."""
        r = _analyze(url="http://qa-probe.invalid/page", capture="true")
        assert r.status_code == 500

    def test_unreachable_url_error_becomes_content(self):
        r = _analyze(url="http://qa-probe.invalid/page")
        body = r.text if r.ok else ""
        assert r.status_code >= 500 or "Failed to fetch" not in body


class TestS2Ssrf:
    """S2: fetcher follows arbitrary URLs and echoes fetched text."""

    @pytest.mark.xfail(reason="DEFECT S2: /analyze url reaches internal hosts and "
                              "echoes page_text_preview (reproduced against own "
                              ":8080/health 2026-10-14). v0.3 egress policy -> XPASS")
    def test_internal_target_blocked(self):
        internal = os.environ.get("SSRF_TEST_URL", "http://127.0.0.1:8080/health")
        r = _analyze(url=internal)
        norm = r.text.replace("\\", "").replace(" ", "")
        leaked = '"status":"ok"' in norm
        assert r.status_code == 400 or not leaked, r.text[:300]


class TestS1BaselineIntegrity:
    """S1: baselines are anonymously writable/overwritable. Uses a probe URL only,
    always cleans up. Requires WRITE_PROBES=1 (mutates the target)."""

    @pytest.mark.skipif(os.environ.get("WRITE_PROBES") != "1",
                        reason="mutates baselines; set WRITE_PROBES=1")
    @pytest.mark.xfail(reason="DEFECT S1: capture_baseline silently overwrites "
                              "existing baseline, no auth/confirm/history "
                              "(verified 2026-10-14). v0.3 promote-flow -> XPASS")
    def test_overwrite_requires_confirmation(self):
        try:
            r1 = _analyze(url=PROBE_URL, capture="true")
            assert r1.status_code == 200, r1.text[:200]
            key = r1.json().get("baseline_key")
            r2 = _analyze(url=PROBE_URL, capture="true")
            overwritten = r2.status_code == 200 and r2.json().get("baseline_key") == key
            assert not overwritten, "silent anonymous overwrite succeeded"
        finally:
            _cleanup(key)


def _cleanup(key):
    """Remove a probe baseline entry from the on-host index (test hygiene)."""
    if not key:
        return
    # Best-effort: only meaningful when tests run ON the deploy host.
    try:
        import json
        from pathlib import Path
        idx_path = Path(os.environ.get("BASELINES_INDEX", "baselines/index.json"))
        if idx_path.exists():
            idx = json.loads(idx_path.read_text())
            bl = idx.get("baselines", idx)
            if key in bl:
                del bl[key]
                idx_path.write_text(json.dumps(idx, indent=2))
                p = Path(idx_path.parent) / f"{key}.png"
                p.unlink(missing_ok=True)
    except Exception:
        pass  # remote-run case: cleanup done out-of-band


class TestS7EventLoop:
    """S7: blocking I/O in async endpoints freezes /health."""

    @pytest.mark.gpu
    def test_health_responsive_under_concurrency(self):
        lats, stop = [], False

        def poll():
            while not stop:
                t = time.time()
                try:
                    requests.get(f"{BASE}/health", timeout=30)
                except Exception:
                    pass
                lats.append(time.time() - t)
                time.sleep(0.25)

        th = threading.Thread(target=poll)
        th.start()
        calls = [threading.Thread(target=lambda: _analyze(url=PROBE_URL, visual="true"))
                 for _ in range(2)]
        [c.start() for c in calls]
        [c.join(timeout=300) for c in calls]
        stop = True
        th.join(timeout=5)
        worst = max(lats) if lats else 99
        assert worst < 1.0, f"/health stalled {worst:.1f}s under 2 concurrent checks"


class TestS4StructuredScore:
    """S4: confidence must be a parsed, numeric, machine-readable field.
    Pins the v0.5 API contract so its flip gates the tag (Sammy checklist).
    Contract: GET /checks/latest -> {score:int 0-100, verdict:enum, differences:list}
    plus verdict MUST be derivable from score via published thresholds (Catch-2);
    the model's own verdict field is advisory only."""

    @pytest.mark.xfail(reason="DEFECT S4: no structured score field anywhere in "
                              "seed responses (verification is prose-only; "
                              "verified 2026-10-14). v0.5 JSON-schema output -> XPASS")
    def test_checks_latest_contract(self):
        r = requests.get(f"{BASE}/checks/latest", timeout=15)
        assert r.status_code == 200, r.status_code
        j = r.json()
        assert isinstance(j.get("score"), int) and 0 <= j["score"] <= 100
        assert isinstance(j.get("verdict"), str) and j["verdict"]
        assert isinstance(j.get("differences"), list)

    @pytest.mark.xfail(reason="DEFECT S4/Catch-2: verdict must be DERIVED from "
                              "score by published threshold table, never trusted "
                              "from the model (measured disagreement: score=4 with "
                              "verdict=Suspicious, 6/6 calls). v0.5 -> XPASS")
    def test_verdict_consistent_with_score(self):
        r = requests.get(f"{BASE}/checks/latest", timeout=15)
        j = r.json()
        assert r.status_code == 200
        # Published thresholds (v0.5): >=90 Healthy, >=60 Minor, >=30 Significant, else Defaced
        s, v = j["score"], j["verdict"]
        expected = ("Healthy" if s >= 90 else "Minor" if s >= 60
                    else "Significant" if s >= 30 else "Defaced")
        assert v == expected, f"score={s} verdict={v} expected={expected}"


class TestS2bRedirectSSRF:
    """S2b (Sammy, planned v0.3 gate): a target allowlist that validates the
    request URL but follows redirects gets 302'd past the blocklist. The v0.3
    filter MUST be per-hop (or redirects disabled), plus resolve-pin against
    DNS rebinding (sibling case: hostname that resolves differently on second
    lookup — needs a controllable resolver, tracked in suite backlog).

    Needs the QA redirector on the deploy host:
        python tests/helpers/redirector.py 8012   # 302s everything -> :8080/health
    then set REDIRECTOR=1 REDIRECT_URL=http://127.0.0.1:8012/r
    """

    @pytest.mark.skipif(os.environ.get("REDIRECTOR") != "1",
                        reason="start tests/helpers/redirector.py, set REDIRECTOR=1")
    @pytest.mark.xfail(reason="DEFECT S2b: redirect bypass of target allowlist "
                              "(planned v0.3 per-hop validation)")
    def test_redirect_to_internal_blocked(self):
        redirect = os.environ.get("REDIRECT_URL", "http://127.0.0.1:8012/r")
        r = _analyze(url=redirect)
        norm = r.text.replace("\\", "").replace(" ", "")
        leaked = '"status":"ok"' in norm
        assert r.status_code == 400 or not leaked, r.text[:300]


class TestS8DownscaleCrush:
    """S8 (Paul, Layer-3 finding 2026-10-15): verify-path `image_to_base64`
    thumbnail-fits full-page screenshots into 1280px — a 1280x7463 baseline
    becomes ~219px wide, so defacement on any long page is unreadable to the
    VLM and silently scores healthy. Fix = band tiling + fail-loud aggregation
    (MUST_CLOSED[v0.2.2]).

    Gate: synthetic ~5400px page, readable tile #4; baseline captured healthy,
    then live variant swapped to a red HACKED banner in tile #4. The verify
    path MUST notice. Requires the tall-page server ON the app host:
        python tests/helpers/tallpage_server.py 8013
    then set TALLPAGE=1 TALLPAGE_URL=http://127.0.0.1:8013/page
    """

    @pytest.mark.gpu
    @pytest.mark.skipif(os.environ.get("TALLPAGE") != "1",
                        reason="start tests/helpers/tallpage_server.py, set TALLPAGE=1")
    @pytest.mark.xfail(reason="DEFECT S8: thumbnail-fit crushes tall pages; verify "
                              "path blind to tile-N defacement (app.py:220-222, "
                              "baseline 1280x7463 -> ~219px). v0.2.2 tiling -> XPASS")
    def test_tall_page_tile4_defacement_detected(self):
        srv = os.environ.get("TALLPAGE_URL", "http://127.0.0.1:8013/page").replace("/page", "")
        r = requests.get(f"{srv}/heal", timeout=10)
        assert r.ok
        cap = _analyze(url=f"{srv}/page", capture="true")
        assert cap.status_code == 200, cap.text[:200]
        assert requests.get(f"{srv}/deface", timeout=10).ok
        try:
            v = _analyze(url=f"{srv}/page", visual="true", timeout=300)
            assert v.status_code == 200, v.text[:200]
            body = v.text.upper()
            noticed = ("HACKED" in body or "DEFAC" in body
                       or "BANNER" in body or "SIGNIFICANT" in body)
            assert noticed, f"verify path blind to tile-4 defacement: {body[:200]}"
        finally:
            requests.get(f"{srv}/heal", timeout=10)


class TestS10NoPairwiseAnywhere:
    """S10 (Sammy finding, QA-pinned): the det-is-None (no screenshot) branch
    still shipped the pairwise VERIFY_PROMPT_TEMPLATE with unframed page_text —
    the measured laundering prompt (blanked page -> 'score 100, Healthy') and
    the last S3 model-input injection sink. Gate is mechanical: once the
    pairwise template is deleted from the narrator path, remove the xfail.
    """

    @pytest.mark.xfail(strict=True, reason="S10 open in v0.4.2: VERIFY_PROMPT_TEMPLATE still formatted at the det-is-None branch")
    def test_no_pairwise_prompt_format_call_remains(self):
        src = (Path(__file__).resolve().parent.parent / "app.py").read_text()
        assert "VERIFY_PROMPT_TEMPLATE.format(" not in src, (
            "pairwise verification prompt is still sent somewhere — "
            "narrator must be single-image on every branch (diff supplies comparison)")


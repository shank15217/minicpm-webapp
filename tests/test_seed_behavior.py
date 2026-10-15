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
    @pytest.mark.xfail(reason="DEFECT S6: dead llama-server yields HTTP 200 with "
                              "'Error calling llama-server' prose in description "
                              "(verified 2026-10-14; flips XPASS when v0.2 lands)")
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

    @pytest.mark.xfail(reason="DEFECT S6: normal path with unreachable URL returns "
                              "HTTP 200; 'Failed to fetch' text is fed to the VLM as "
                              "page content (reproduced 2026-10-14). v0.2 typed "
                              "errors -> XPASS")
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
    @pytest.mark.xfail(reason="DEFECT S7: 2 concurrent /analyze froze /health to "
                              "max 13.9s vs ~8ms idle (measured 2026-10-14). "
                              "v0.2 httpx/async path -> XPASS")
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

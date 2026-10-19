"""S2/S2b SSRF gate pins (external review 2026-10-19; Quinn resolved-IP pin).

Gate rules under test, in order: metadata-class IPs refused ALWAYS (even
verbatim, even under '*'); IP literals must match the allowlist verbatim;
GLOB-matched hostnames are resolved and refused when any answer is
loopback/link-local (DNS-rebinding shape); verbatim-named hosts pass.
S1: MPW_ADMIN_TOKEN gates the baseline-write path only.
"""
import sys
from pathlib import Path

import pytest
from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as app_mod  # noqa: E402


@pytest.fixture
def defaults(monkeypatch):
    monkeypatch.setenv("MPW_ALLOW_TARGETS", "*.nellienet.win,127.0.0.1,localhost,::1")


def test_internal_ip_literal_refused_by_default(defaults):
    with pytest.raises(app_mod.CheckFailed) as e:
        app_mod.check_target("http://192.168.1.1/admin")
    assert e.value.code == "TARGET_NOT_ALLOWED"


def test_wildcard_host_allowed(defaults):
    app_mod.check_target("https://anything.nellienet.win/x")  # must not raise


def test_metadata_ip_refused_even_with_star(monkeypatch):
    monkeypatch.setenv("MPW_ALLOW_TARGETS", "*")
    with pytest.raises(app_mod.CheckFailed) as e:
        app_mod.check_target("http://169.254.169.254/latest/meta-data/")
    assert e.value.code == "TARGET_NOT_ALLOWED"


def test_rebinding_wildcard_to_loopback_refused(monkeypatch):
    import socket
    monkeypatch.setenv("MPW_ALLOW_TARGETS", "*.nellienet.win")
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda *a, **k: [(2, 1, 6, "", ("127.0.0.1", 0))])
    with pytest.raises(app_mod.CheckFailed) as e:
        app_mod.check_target("https://evil.nellienet.win/")
    assert "loopback" in str(e.value)


def test_per_hop_hook_refuses_metadata_hop(monkeypatch):
    import anyio
    import httpx
    monkeypatch.setenv("MPW_ALLOW_TARGETS", "*.nellienet.win")

    async def go():
        transport = httpx.MockTransport(
            lambda req: (httpx.Response(302, headers={"location": "http://169.254.169.254/"})
                         if req.url.host == "ok.nellienet.win"
                         else httpx.Response(200, text="stolen")))
        client = httpx.AsyncClient(transport=transport, follow_redirects=True,
                                   event_hooks={"request": [app_mod._ssrf_httpx_hook]})
        try:
            with pytest.raises(app_mod.CheckFailed):
                await client.get("https://ok.nellienet.win/")
        finally:
            await client.aclose()
    anyio.run(go)


def test_s1_baseline_requires_admin_token(monkeypatch):
    monkeypatch.setenv("MPW_ADMIN_TOKEN", "sekret")
    with TestClient(app_mod.app) as c:
        r = c.post("/analyze", data={"mode": "detail", "url": "https://x.nellienet.win/",
                                     "capture_baseline": "true",
                                     "visual_verify": "true",
                                     "allow_insecure": "false"})
        assert r.status_code == 401 and r.json()["error_code"] == "ADMIN_TOKEN_REQUIRED"
        r2 = c.post("/analyze", data={"mode": "detail", "url": "https://x.nellienet.win/",
                                      "capture_baseline": "true",
                                      "visual_verify": "true",
                                      "allow_insecure": "false"},
                    headers={"X-Admin-Token": "sekret"})
        assert r2.status_code != 401  # token accepted; later stages may fail however


def test_read_paths_stay_open():
    with TestClient(app_mod.app) as c:
        assert c.get("/health").status_code == 200
        assert c.get("/baselines").status_code == 200


def test_refusal_contract_on_the_socket(defaults):
    """Sammy defect pin: entry-gate refusals are named JSON, never bare 500."""
    with TestClient(app_mod.app) as c:
        r = c.post("/analyze", data={"url": "http://169.254.169.254/latest/meta-data/"})
        assert r.status_code == 403
        body = r.json()
        assert body["error_code"] == "TARGET_NOT_ALLOWED"
        assert body["status"] == "REJECTED"

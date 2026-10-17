"""Unauthenticated-baseline guard (Sammy CRITICAL, Quinn confirmed, 2026-10-17).

capture_baseline + allow_insecure must NEVER persist a baseline: an
unauthenticated reference inverts the monitor permanently (genuine site
reads 'changed', matching defacement reads OK, VLM description authored
from MitM content). allow_insecure is for consuming broken sites only.

Offline: pure finding-class logic. The 409 path runs live on the prod box
against the expired-cert fixture: MPW_TLS_FIXTURE=1 pytest tests/test_baseline_tls_guard.py
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import _unreachable_finding


@pytest.mark.parametrize("code,sev", [
    ("DNS_FAILURE", "finding"),      # takeover-shaped: alert
    ("SSL_CERT_EXPIRED", "degraded"),  # usually neglect: named degraded state
    ("SSL_CERT_UNTRUSTED", "degraded"),
    ("TIMEOUT", None),               # plumbing stays plumbing
    ("HTTP_503", None),
    ("CAPTURE_FAILED", None),
])
def test_transition_finding_classes(code, sev):
    f = _unreachable_finding("https://bank.example", code, last_good=True)
    assert (f or {}).get("severity") == sev
    # without a recorded last-good, unreachable is not a transition: no finding
    assert _unreachable_finding("https://bank.example", code, last_good=False) is None


@pytest.mark.skipif(os.environ.get("MPW_TLS_FIXTURE") != "1",
                    reason="prod-box test: needs expired-cert https server "
                           "on 127.0.0.1:8443 (see RUNBOOK fixture)")
def test_baseline_with_insecure_capture_409_and_no_write():
    from starlette.testclient import TestClient
    import app as appmod

    writes = []
    llm_calls = []
    orig = appmod.save_baseline
    orig_llm = appmod.call_llama

    async def _spy_llama(*a, **k):
        llm_calls.append(a)
        return await orig_llm(*a, **k)

    appmod.save_baseline = lambda *a, **k: (writes.append(a), "KEY")[1]
    appmod.call_llama = _spy_llama
    try:
        c = TestClient(appmod.app)
        # control: the fixture IS reachable with cert checks off
        r = c.post("/analyze", data={
            "url": "https://127.0.0.1:8443/",
            "capture_baseline": "true",
            "allow_insecure": "true",
            "visual_verify": "true",
        })
        assert r.status_code == 409, r.text
        assert r.json()["error_code"] == "UNAUTHENTICATED_BASELINE_REJECTED"
        assert writes == [], "save_baseline must not run for unauthenticated capture"
        # Sammy variant: rejection must precede VLM spend — a guard that
        # narrates first (correct 409, wasted tokens, MitM content already
        # described in memory) fails this assertion, not just the write check.
        assert llm_calls == [], "call_llama must never run for a rejected baseline"
        # run_history contract for the post-flight (precise, so a CORRECT
        # impl does not fail a wrong assertion): OK-row count for the URL is
        # unchanged; exactly one NEW row appears with error_code
        # UNAUTHENTICATED_BASELINE_REJECTED — recording the rejection is by
        # design, "zero new rows" would be a wrong assertion.
    finally:
        appmod.save_baseline = orig
        appmod.call_llama = orig_llm

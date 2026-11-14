"""Seam pins for decisionlib (d1 /v1/systemone wire contract).

Fixtures were recorded VERBATIM from the live tower on 2026-11-13
(build 12374, commit 9183a552e, pvellm2:8095, LAN path). If a llama-server
upgrade renames/re-nests these fields, the parser tests below fail HERE —
the intent is that no prod verdict ever rides a silently-defaulted parse.
"""
import json
import os
from pathlib import Path

import pytest

import decisionlib
from decisionlib import WireFormatError, parse_systemone, tower_url

FIX = Path(__file__).parent / "fixtures"


def _load(name):
    return json.loads((FIX / name).read_text())


# ---- recorded-shape pins ----------------------------------------------------

def test_noul_fixture_parses_exact():
    got = parse_systemone(_load("systemone_noul.json"))
    assert got["match"] == {"kind": "noul", "score": 0.277199824338505}


def test_choice_fixture_parses_exact():
    got = parse_systemone(_load("systemone_choice.json"))
    c = got["color"]
    assert c["kind"] == "choice" and c["choice"] == "red"
    assert c["confidence"] == c["probabilities"]["red"]  # mirror law, per card


def test_output_tokens_nonzero_is_not_d1():
    p = _load("systemone_noul.json")
    p["usage"]["output_tokens"] = 7  # something that GENERATES is not the decision model
    with pytest.raises(WireFormatError):
        parse_systemone(p)


@pytest.mark.parametrize("mutate", [
    lambda p: p["answers"]["match"].pop("noul"),                      # rename
    lambda p: p["answers"]["match"].__setitem__("noul", "0.27"),       # type drift
    lambda p: p["answers"]["match"].__setitem__("noul", {"p": 0.27}),  # re-nest
    lambda p: p.pop("usage"),
    lambda p: p["answers"]["match"].__setitem__("type", "bool"),
])
def test_wire_rename_fails_loud(mutate):
    p = mutate(_load("systemone_noul.json"))
    with pytest.raises(WireFormatError):
        parse_systemone(p)


def test_choice_confidence_mismatch_fails():
    p = _load("systemone_choice.json")
    p["answers"]["color"]["confidence"] = 0.5
    with pytest.raises(WireFormatError):
        parse_systemone(p)


# ---- canonicalizer: D9 law generalized --------------------------------------

@pytest.mark.parametrize("bare,expect", [
    ("http://h:8095", "http://h:8095/v1/systemone"),
    ("http://h:8095/", "http://h:8095/v1/systemone"),
    ("http://h:8095/v1", "http://h:8095/v1/systemone"),
    ("http://h:8095/v1/systemone", "http://h:8095/v1/systemone"),
    ("", ""),
])
def test_tower_url_systemone(bare, expect):
    assert tower_url(bare, "systemone") == expect


def test_tower_url_parity_with_app_chat_canonicalizer():
    """decisionlib.tower_url(kind='chat/completions') must never drift from
    app._tower_chat_url — one canonicalizer law (D9), two call sites."""
    import app
    for u in ("http://h:8080", "http://h:8080/", "http://h:8080/v1",
              "http://h:8080/v1/chat/completions", ""):
        assert tower_url(u, "chat/completions") == app._tower_chat_url(u), u


# ---- band law: verdict from number ------------------------------------------

def test_band_verdict_boundaries():
    assert decisionlib.band_verdict(decisionlib.BAND_HI) == "MATCH"
    assert decisionlib.band_verdict(decisionlib.BAND_LO) == "NO_MATCH"
    assert decisionlib.band_verdict(0.5) == "UNCERTAIN"
    with pytest.raises(WireFormatError):
        decisionlib.band_verdict(1.0000001)


def test_verify_match_refuses_empty_state_and_url():
    from decisionlib import D1Error, verify_match
    with pytest.raises(D1Error):
        verify_match(b"png", "", "http://x/v1/systemone")
    with pytest.raises(D1Error):
        verify_match(b"png", "a frozen narration", "")


# ---- live determinism pin (Quinn's law), D11 skip-with-receipt --------------

_LIVE = None


def _live():
    global _LIVE
    if _LIVE is None:
        url = os.environ.get("MPW_D1_URL")
        if not url:
            _LIVE = False
        else:
            import urllib.request
            try:
                urllib.request.urlopen(url, timeout=3).read()
                _LIVE = False  # GET on POST-only endpoint still proves the port is live
            except Exception as e:  # URLError w/ code 405-family = live; refused = dead
                _LIVE = "405" in str(e) or "HTTP Error" in str(e)
    return _LIVE


@pytest.mark.skipif(not _live(), reason=(
    "D11 receipt: MPW_D1_URL unset/unreachable — determinism pin needs the live "
    "tower; env absent, not code broken."))
def test_live_determinism_bit_identical():
    """Same (pixels, description) -> bit-identical score, twice. Any movement
    is OUR bug (QA law 2026-11-13): zero-token decision is deterministic."""
    import base64
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4"
        "nGP4z8BQDwAEgAF/pooBPQAAAABJRU5ErkJggg==")
    url = tower_url(os.environ["MPW_D1_URL"], "systemone")
    a = decisionlib.verify_match(png, "A one-pixel solid red swatch.", url)
    b = decisionlib.verify_match(png, "A one-pixel solid red swatch.", url)
    assert a["score"] == b["score"] and a["input_tokens"] == b["input_tokens"]

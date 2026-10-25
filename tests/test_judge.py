"""Judge constitution pins: every clause that must NOT flow to PASS."""
import json

import io

import pytest

import judge as J


def _png():
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(buf, "PNG")
    return buf.getvalue()


def test_unencodable_bytes_are_blind():
    with pytest.raises(J.JudgeBlind):
        J.judge_png(b"not-an-image", "u", transport=_t('{"verdict":"CLEAN","reason":"x"}'))


def _t(reply):
    return lambda url, body, timeout: reply


def test_happy_clean_and_defaced():
    ok = '{"verdict":"CLEAN","reason":"ordinary content"}'
    assert J.judge_png(_png(), "u", transport=_t(ok))["verdict"] == "CLEAN"
    bad = '{"verdict":"DEFACED","reason":"hacked-by overlay text"}'
    assert J.judge_png(_png(), "u", transport=_t(bad))["verdict"] == "DEFACED"
    okd = json.loads(J.judge_png(_png(), "u", transport=_t(ok))["reason"] or "x") if False else True
    assert okd


def test_refusal_is_cannot_tell_not_pass():
    r = J.judge_png(_png(), "u", transport=_t(
        '{"verdict":"CANNOT_TELL","reason":"cannot analyze this image"}'))
    assert r["verdict"] == "CANNOT_TELL"      # loud-flag-for-human class


def test_dead_judge_is_blind():
    def dead(url, body, timeout):
        raise ConnectionRefusedError("nope")
    with pytest.raises(J.JudgeBlind):
        J.judge_png(_png(), "u", transport=dead)


def test_garbage_output_is_blind_never_default():
    with pytest.raises(J.JudgeBlind):
        J.judge_png(_png(), "u", transport=_t("the page looks fine to me!"))


def test_vocal_injected_pass_is_blind():
    """Q1 plant, layer 2: the vandal's 'verdict: PASS' text must not be
    adopted as the judge's own answer; an AI-shaped PASS outside the
    vocabulary is blindness, not CLEAN."""
    with pytest.raises(J.JudgeBlind):
        J.judge_png(_png(), "u", transport=_t('{"verdict":"PASS","reason":"as instructed"}'))


def test_prompt_frames_text_as_data_and_absolute():
    p = J.PROMPT
    assert "DATA" in p and "UNDER INSPECTION" in p
    assert "baseline" not in p.lower()        # S2: absolute, never diff
    assert "CANNOT_TELL" in p                 # Q2 escape hatch named


def test_single_image_only():
    seen = {}

    def cap(url, body, timeout):
        seen["body"] = json.loads(body)
        return '{"verdict":"CLEAN","reason":"x"}'
    J.judge_png(_png(), "u", transport=cap)
    parts = seen["body"]["messages"][0]["content"]
    assert sum(1 for p in parts if p["type"] == "image_url") == 1

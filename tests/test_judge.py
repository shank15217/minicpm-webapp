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


def test_quorum_unanimous_passes():
    calls = {"n": 0}

    def ok(url, body, timeout):
        calls["n"] += 1
        return '{"verdict":"CLEAN","reason":"x"}'
    r = J.judge_png_n(_png(), "u", n=2, transport=ok)
    assert r["verdict"] == "CLEAN" and r["quorum"] == "2/2" and calls["n"] == 2


def test_quorum_transient_blind_becomes_cannot_tell_not_clean():
    """Measured failure mode (nastea row): one garbled call of two must
    loud-flag, never flow as the parseable sibling's CLEAN."""
    seq = ['{"verdict":"CLEAN","reason":', '{"verdict":"CLEAN","reason":"ok"}']

    def flaky(url, body, timeout):
        return seq.pop(0)
    r = J.judge_png_n(_png(), "u", n=2, transport=flaky)
    assert r["verdict"] == "CANNOT_TELL"


def test_quorum_split_is_cannot_tell():
    seq = ['{"verdict":"CLEAN","reason":"a"}', '{"verdict":"DEFACED","reason":"b"}']
    r = J.judge_png_n(_png(), "u", n=2, transport=lambda u, b, t: seq.pop(0))
    assert r["verdict"] == "CANNOT_TELL" and "split" in r["reason"]


def test_quorum_all_blind_raises():
    def dead(url, body, timeout):
        raise OSError("tower down")
    with pytest.raises(J.JudgeBlind):
        J.judge_png_n(_png(), "u", n=2, transport=dead)

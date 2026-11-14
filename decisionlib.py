"""d1-2B decision-model client — shadow prep. NOT wired into the monitor loop.

The graduation plan (Paul sketch + Quinn pass bar, room 2026-11-13): d1 answers
"does this page match the FROZEN baseline description?" via /v1/systemone
(single forward pass, zero generated tokens, bit-deterministic). Until the
labeled-set eval strictly dominates analytics on churn-vs-defacement, nothing
in this module may flip a verdict; the analytics path stays binding.

Laws pinned here (each has a test in tests/test_decisionlib_seam.py):
- STATE NEVER COMES FROM THE PAGE: `description` is the frozen baseline
  narration; passing page text as state is the injection door, not a feature.
- VERDICT FROM NUMBER, NEVER PROSE: verdict derives from the calibrated float
  via the published band table (Catch-2 rule, same law v0.5 set for scores).
- WIRE FORMAT IS A CONTRACT, NOT A SUGGESTION: a llama-server upgrade that
  renames or re-nests response fields raises WireFormatError LOUD. Silent
  defaults that read "match" on a parse miss would be D10 in a new costume.
- BLIND != QUIET: connection-level failure raises D1Unreachable; the caller
  must log loud and keep the analytics verdict binding (mpw_d1_ok gauge rides
  the shadow-landing commit, not this file).

Wire format (recorded live 2026-11-13 from build 12374 @ pvellm2:8095;
fixtures tests/fixtures/systemone_*.json):
  noul   -> answers.<q> = {"type":"noul",   "noul": <float>}
  choice -> answers.<q> = {"type":"choice", "choice": <str>,
                           "probabilities": {...}, "confidence": <float>}
  usage  = {"input_tokens": int, "output_tokens": 0}   # 0 is the law, not an accident
"""
from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request

# Provisional bands for shadow journaling ONLY — final numbers come from the
# labeled eval (Quinn's harness), not from this file. Three bands because the
# eval needs an explicit UNCERTAIN bucket; a binary cut hides the hard cases.
BAND_HI = 0.65
BAND_LO = 0.35


class D1Error(RuntimeError):
    pass


class D1Unreachable(D1Error):
    """Connection-level failure. Caller: gauge to 0, page loud, analytics stays binding."""


class WireFormatError(D1Error):
    """Response shape diverged from the recorded contract. Fail loud, never default."""


def tower_url(u: str, kind: str = "systemone") -> str:
    """D9's canonicalizer generalized: bare origin, /v1, or full endpoint all
    land on the exact endpoint. kind is 'systemone' or 'chat/completions'.
    Parity with app._tower_chat_url for the chat kind is asserted in the seam
    test so the two can never drift into D9's ghost."""
    u = (u or "").strip().rstrip("/")
    if not u:
        return ""
    suffix = "/v1/" + kind
    if u.endswith(suffix):
        return u
    if u.endswith("/v1"):
        return u + "/" + kind
    return u + suffix


def band_verdict(score: float) -> str:
    """Verdict FROM NUMBER via the published table — no model prose anywhere."""
    if not 0.0 <= score <= 1.0:
        raise WireFormatError(f"noul score out of [0,1]: {score!r}")
    if score >= BAND_HI:
        return "MATCH"
    if score <= BAND_LO:
        return "NO_MATCH"
    return "UNCERTAIN"


def parse_systemone(payload: dict) -> dict:
    """Strict parse of the recorded contract. Any shape miss raises."""
    if not isinstance(payload, dict):
        raise WireFormatError(f"payload is {type(payload).__name__}, expected object")
    usage = payload.get("usage")
    if not isinstance(usage, dict) or "input_tokens" not in usage:
        raise WireFormatError(f"missing usage.input_tokens: {payload!r:.200}")
    if usage.get("output_tokens") != 0:
        # zero generated tokens is the decision-model contract; a nonzero here
        # means we are talking to something that is NOT d1-in-decision-mode.
        raise WireFormatError(f"output_tokens={usage.get('output_tokens')!r}, expected 0")
    answers = payload.get("answers")
    if not isinstance(answers, dict) or not answers:
        raise WireFormatError(f"missing answers: {payload!r:.200}")
    out = {}
    for name, ans in answers.items():
        if not isinstance(ans, dict) or "type" not in ans:
            raise WireFormatError(f"answers.{name} malformed: {ans!r:.200}")
        t = ans["type"]
        if t == "noul":
            v = ans.get("noul")
            if not isinstance(v, float):
                raise WireFormatError(f"answers.{name}.noul is {type(v).__name__}, expected float")
            out[name] = {"kind": "noul", "score": v}
        elif t == "choice":
            p = ans.get("probabilities")
            c = ans.get("choice")
            conf = ans.get("confidence")
            if not isinstance(p, dict) or not p or not isinstance(c, str) or not isinstance(conf, float):
                raise WireFormatError(f"answers.{name} choice shape drifted: {ans!r:.200}")
            if abs(max(p.values()) - conf) > 1e-12:
                raise WireFormatError(f"answers.{name}: confidence {conf} != max prob {max(p.values())}")
            if c not in p:
                raise WireFormatError(f"answers.{name}: choice {c!r} not in probabilities")
            out[name] = {"kind": "choice", "choice": c, "probabilities": p, "confidence": conf}
        else:
            raise WireFormatError(f"answers.{name}: unsupported type {t!r}")
    return out


def verify_match(png: bytes, description: str, url: str, timeout: float = 120.0) -> dict:
    """One noul question: does the screenshot match the frozen description?

    `description` MUST be the baseline-time frozen narration (freeze-with-
    baseline law). Returns {"score": float, "verdict": str, "input_tokens": int}.
    """
    if not description or not description.strip():
        raise D1Error("empty description: the state side is the frozen narration, never ''")
    if not url:
        raise D1Error("d1 url unset (MPW_D1_URL): config-fed gate law — no silent localhost default")
    req_body = {
        "state": description,
        "images": ["data:image/png;base64," + base64.b64encode(png).decode()],
        "questions": {"match": {"type": "noul",
                                "instructions": "Does this page match the description?"}},
    }
    req = urllib.request.Request(
        url, data=json.dumps(req_body).encode(),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.load(resp)
    except (urllib.error.URLError, OSError) as e:
        raise D1Unreachable(f"d1 unreachable at {url}: {e}") from e
    parsed = parse_systemone(payload)
    m = parsed.get("match")
    if not m or m["kind"] != "noul":
        raise WireFormatError("answers.match noul missing from verify response")
    return {"score": m["score"], "verdict": band_verdict(m["score"]),
            "input_tokens": payload["usage"]["input_tokens"]}

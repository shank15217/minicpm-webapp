"""anchorcheck — content-anchor mode for bands the pixel channel can't police.

Calibration protocol §2/§4 + Sammy's measured finding: on fhlb's rotating
hero a 1.3%-area banner moved band-0 sim by **0.016** against a rotation
noise spread of **0.14** — the pixel channel is blind in BOTH directions on
dynamic bands (a clean capture can alert while the defaced one passes).
Tolerance-widening can't fix blindness; the band needs a second channel
anchored to *content* rather than geometry.

Doctrinal boundary (Act 6 — the reason this file is written the way it is):
the model answers ONLY single-image factual presence questions. It is never
shown a baseline, never asked same-or-different, never asked for a score or
a health verdict. The comparison is not "removed and trusted to the model";
the alert rule below compares the parsed booleans against a per-band
EXPECTED-FACTS profile and computes the delta mechanically, in pure Python.
Facts from the VLM, judgments from the diff — no laundering direction exists
in this code path.

This module contains NO network I/O so the mechanical half is unit-testable
without a model. `scripts/anchor_mini_cal.py` runs the model half.
"""
from __future__ import annotations

CHECKLIST_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "anchor_facts",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "has_text": {"type": "boolean"},
                "has_branding": {"type": "boolean"},
                "has_navigation": {"type": "boolean"},
                "is_blank": {"type": "boolean"},
                "text_sample": {"type": "string"},
            },
            "required": ["has_text", "has_branding", "has_navigation",
                         "is_blank", "text_sample"],
            "additionalProperties": False,
        },
    },
}

# Deliberately narrow: no score, no verdict, no comparison verb. A model that
# cannot express "identical"/"healthy" cannot launder with them.
ANCHOR_PROMPT = (
    "Look at this single image region from a web page. Answer only these "
    "factual questions about what is visible: has_text (is any readable text "
    "visible), has_branding (is a logo, wordmark or brand name visible), "
    "has_navigation (are menu or nav links visible), is_blank (is the region "
    "empty, solid color, or containing no content at all). Put a short "
    "verbatim quote of readable text you actually see in text_sample, or an "
    "empty string if there is none. Do not describe the page, do not judge "
    "quality or health, do not compare to anything.")


def parse_facts(content: str) -> dict:
    """Parse constrained-JSON model output into a facts dict.

    Strict json_schema output is already valid JSON; anything else is an
    instrument failure, NOT a negative answer — return ok=False so callers
    fail loud instead of defaulting to innocent.
    """
    import json
    try:
        d = json.loads(content)
    except Exception as e:
        return {"ok": False, "error": f"unparseable anchor output: {e}",
                "raw": (content or "")[:300]}
    need = {"has_text", "has_branding", "has_navigation", "is_blank", "text_sample"}
    missing = need - set(d)
    if missing:
        return {"ok": False, "error": f"anchor output missing keys: {sorted(missing)}",
                "raw": (content or "")[:300]}
    return {
        "ok": True,
        "has_text": bool(d["has_text"]),
        "has_branding": bool(d["has_branding"]),
        "has_navigation": bool(d["has_navigation"]),
        "is_blank": bool(d["is_blank"]),
        "text_sample": str(d["text_sample"])[:200],
    }


def check_facts(facts: dict, expected: dict) -> dict:
    """Mechanical alert rule: expected facts vs reported facts. Pure Python.

    expected: {"requires_text": bool, "requires_branding": bool,
               "requires_navigation": bool}  (per-band, from the calibrated
               profile — facts the band held across attested control runs).
    Any required fact that went missing, or an is_blank claim on a band that
    must carry content, is an alert. There is no direction in which the model
    can volunteer a PASS: pass = absence of a reported loss, and a fact it
    fails to report is caught by the profile it contradicts.
    """
    if not facts.get("ok"):
        return {"verdict": "ANCHOR_UNAVAILABLE", "missing": [],
                "detail": facts.get("error", "unknown")}
    missing = []
    if expected.get("requires_text") and (not facts["has_text"] or facts["is_blank"]):
        missing.append("text")
    if expected.get("requires_branding") and (not facts["has_branding"] or facts["is_blank"]):
        missing.append("branding")
    if expected.get("requires_navigation") and (not facts["has_navigation"] or facts["is_blank"]):
        missing.append("navigation")
    return {"verdict": "ANCHOR_LOSS" if missing else "ANCHOR_OK",
            "missing": missing,
            "text_sample": facts["text_sample"]}

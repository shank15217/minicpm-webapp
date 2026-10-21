"""Tower-orientation pin (stdlib only; run ON pvellm with MPW_TOWER_PROBE=1).

The Oct-15 receipt (":8080 = 837 tokens = detail") (RETRACTED) was falsified on
2026-10-17 by two independent same-image probes (489 vs 1851 here; 612 vs
2304 on Quinn's) and by the GGUF headers the user diffed: the "-4x" file
(scale_factor=2 => 4x downsample) is the HIGH-detail 256-tok/tile tower;
plain f16 (scale_factor=4, 16x downsample) is the 64-tok/tile general one.

This pin exists so that inversion can never ship again on prose alone: it
MEASURES both towers with one deterministic image and asserts the routing
constants match the measured token orientation. Offline it pins only the
static label/routing text (no network).

Run live:
  MPW_TOWER_PROBE=1 python3 tests/test_tower_orientation.py
(receipt printed; non-zero exit if towers disagree with routing.)
"""
import json
import os
import re
import struct
import sys
import urllib.request
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

FAST_URL = os.getenv("LLAMA_SERVER_FAST", "http://127.0.0.1:8080/v1/chat/completions")
DETAIL_URL = os.getenv("LLAMA_SERVER_DETAIL", "http://127.0.0.1:8081/v1/chat/completions")


def make_png(width: int = 512, height: int = 512) -> bytes:
    """Deterministic gradient PNG, stdlib only — same bytes every run."""
    rows = b"".join(
        b"\x00" + bytes(v for x in range(width) for v in ((x * 255) // width, (y * 255) // height, 128))
        for y in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows, 9))
            + chunk(b"IEND", b""))


def prompt_tokens(url: str, png: bytes) -> int:
    body = json.dumps({
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {
                "url": "data:image/png;base64," + __import__("base64").b64encode(png).decode()}},
            {"type": "text", "text": "Describe this image in one sentence."}]}],
        "max_tokens": 16, "temperature": 0}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    r = json.load(urllib.request.urlopen(req, timeout=180))
    return int(r["usage"]["prompt_tokens"])


def test_static_orientation_labels():
    """Offline pins: routing + UI labels must carry the MEASURED orientation."""
    app = (ROOT / "app.py").read_text()
    # detail must default to :8081, fast to :8080 (env-overridable, default matters)
    assert re.search(r'LLAMA_SERVER_DETAIL.*?8081', app, re.S), "detail must default to :8081"
    assert re.search(r'LLAMA_SERVER_FAST.*?8080', app, re.S), "fast must default to :8080"
    # alias truth: 16x-mode=f16=fast, 4x=-4x=detail (the OLD crossed table must stay dead)
    assert '"16x": "fast"' in app and '"4x": "detail"' in app, "aliases must follow file truth"
    html = (ROOT / "static/index.html").read_text()
    assert "f16 (:8080" in html and "-4x (:8081" in html, "UI labels must name file+port truth"
    # Falsified receipt strings must not return as LIVE claims ANYWHERE —
    # scope widened at the 2026-10-21 doc lint: the original loop covered
    # static/index.html only, which is exactly how the inverted comment in
    # app.py and the 837-claim in DEPLOY.md passed this very file
    # (detector scope, not detector absence — Quinn/Sammy). Any tracked
    # .py/.sh/.md line carries the pin; RETRACT-marked lines are exempt,
    # a bare retracted number outside them fails the gate.
    scan = {"app.py": app, "static/index.html": html}
    for f in sorted(ROOT.rglob("*")):
        if (".git" in f.parts or ".venv" in f.parts or "__pycache__" in f.parts
                or f.suffix not in (".py", ".sh", ".md", ".html")
                or f.name == "uv.lock"):
            continue
        scan[str(f.relative_to(ROOT))] = f.read_text(errors="ignore")
    violations = []
    for fname, text in scan.items():
        violations.extend(dead_line_violations(fname, text))
    assert not violations, ("falsified receipts live (one entry per line):\n"
                            + "\n".join(violations))


# --- exemption semantics (Sammy 2026-10-21: pin them or the next paragraph
# re-wrap at 100 cols resurrects a dead number through the RETRACT hole) ---
# Built via concat so this file's own lines never carry a dead literal.
DEAD_RECEIPTS = ("8" + "37 visual tokens", "~8" + "30 visual tokens",
                 "8" + "37 prompt", "= " + "837",
                 "8" + "32 vs 223", "vs -4x = " + "223")


def dead_line_violations(fname, text):
    """Per-line rule: a retraction is legal ONLY if the RETRACT marker shares
    the physical line with the figure. One entry per offending line even when
    several dead strings hit it (Sammy: repeated lines train people to ignore
    the gate's output)."""
    out = []
    for ln, line in enumerate(text.splitlines(), 1):
        if "RETRACT" in line.upper():
            continue
        if any(d in line for d in DEAD_RECEIPTS):
            out.append(f"{fname}:{ln}: {line.strip()[:90]}")
    return out


def test_retraction_exemption_semantics():
    """Clause-6 doctrine on the linter itself: three planted fixtures pin
    the per-line RETRACT exemption (Quinn acceptance, Sammy amendment)."""
    num = "the detail tower measured 8" + "37 visual tokens"
    # 1) planted inverted line WITHOUT a marker => must fire
    v = dead_line_violations("planted.md", "intro\n> " + num + "\n")
    assert len(v) == 1 and "planted.md:2" in v[0], f"planted live claim slipped: {v}"
    # 2) legitimate retraction: marker on the SAME physical line => must NOT fire
    v = dead_line_violations("honest.md", "prior claim (" + num + ") RETRACTED by re-measure\n")
    assert v == [], f"lawful RETRACT line flagged: {v}"
    # 3) WRAPPED retraction: marker on the previous line, figure alone on the
    #    next (the exact README:26 shape that hid a dead number at 3eb16f0)
    #    => must fire. Re-wrapping a paragraph is not a retraction.
    wrapped = "RETRACTED: the old receipt said\n> " + num + " in the original run\n"
    v = dead_line_violations("wrapped.md", wrapped)
    assert len(v) == 1 and "wrapped.md:2" in v[0], f"wrapped retraction laundered the figure: {v}"
    # dedupe check: one line hit by two dead strings reports ONCE
    two = "x 8" + "37 prompt and = 83" + "7 tokens\n"
    v = dead_line_violations("dedupe.md", two)
    assert len(v) == 1, f"line double-reported: {v}"


def test_live_tower_orientation():
    """Live pin (pvellm only): same image => detail tower spends MORE tokens."""
    if os.getenv("MPW_TOWER_PROBE") != "1":
        return  # offline CI: static pins above are the gate
    png = make_png()
    fast_t, detail_t = prompt_tokens(FAST_URL, png), prompt_tokens(DETAIL_URL, png)
    print(f"MEASURED: fast(:8080)={fast_t} detail(:8081)={detail_t} "
          f"ratio={detail_t / max(fast_t, 1):.2f}")
    assert detail_t > fast_t * 2, (
        f"TOWER ORIENTATION BROKEN: detail({detail_t}) should spend >=2x "
        f"fast({fast_t}) — towers swapped or files changed under us")


if __name__ == "__main__":
    test_static_orientation_labels()
    print("static orientation pins: OK")
    if os.getenv("MPW_TOWER_PROBE") == "1":
        test_live_tower_orientation()
        print("live orientation pin: OK")

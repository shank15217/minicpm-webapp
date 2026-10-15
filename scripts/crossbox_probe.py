#!/usr/bin/env python3
"""crossbox_probe.py — score-stability pre-flight before any v0.5 calibration fan-out.

Runs an identical scenario set against N llama-server (MiniCPM-V) endpoints and
reports per-scenario per-box constrained-JSON scores (v2: json_schema + 1600 tok + finish_reason per row) and cross-box deltas, so
build variance is measured BEFORE it can be baked into a calibration table as
if it were site signal (Sammy requirement #2, 2026-10-14). Doubles as a nightly
engine-drift probe: re-run after every llama.cpp nightly and diff.

Scenarios are generated on the fly from one baseline PNG using PIL:
  S01 identical (baseline vs baseline)          -> expect ~100
  S02 title text swapped (dark rectangle+text)  -> expect low
  S03 banner overlay across middle              -> expect low
  S04 blackout (full black image)               -> expect ~0
  S05 clock-corner noise (16px corner redraw)   -> expect ~100 (benign dynamic)
  S06 2px shift (scroll jitter)                 -> expect ~100
  S07 image placeholder block (gray box)        -> expect mid/low
  S08 contrast wash (brightness 1.4)            -> expect high-mid
  S09 half crop + white fill                    -> expect low
  S10 baseline again (repeat of S01)            -> intra-box determinism check

Score extraction: current VERIFY_PROMPT asks prose for 0-100; we take the
first standalone integer in 0..100. That is deliberately crude — it MEASURES
today's behavior, it does not pretend to be the v0.5 structured output.
Temperature is pinned to 0 (Sammy: single-client bit-exactness at temp 0).

Usage (from anywhere that can reach the boxes' ports):
  python scripts/crossbox_probe.py \
      --endpoint pvellm=http://192.168.35.17:8080/v1/chat/completions \
      --endpoint pvellm2=http://192.168.35.36:8080/v1/chat/completions \
      --endpoint pvellm3=http://192.168.35.22:8080/v1/chat/completions \
      --baseline baselines/8a2620e6cdde2557.png --out probe_results.json

Exit code 0 always unless a box is unreachable (then that box is reported
per-scenario with error strings and overall exit 2). Deltas table prints to
stdout as aligned text plus JSON to --out. stdlib + PIL + httpx only.
"""
import argparse
import base64
import hashlib
import io
import json
import re
import statistics
import sys

import httpx
from PIL import Image

PROMPT_SYSTEM = ("You are a website health / defacement detection assistant "
                 "comparing a baseline reference to a live page.")
PROMPT_USER = ("You are verifying whether a live website matches a stored "
               "baseline reference (for health / defacement monitoring).\n"
               "Baseline reference image: the first image.\n"
               "Live page screenshot: the second image.\n"
               "Compare them and report a similarity/health score 0-100 "
               "(100 = essentially identical and healthy, 0 = completely "
               "different or defaced) plus the main concrete differences you "
               "observe (empty list if none).")

RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "verify",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "score": {"type": "integer", "minimum": 0, "maximum": 100},
                "differences": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["score", "differences"],
            "additionalProperties": False,
        },
    },
}

MAX_SIDE = 1280


def data_url(img: Image.Image) -> str:
    im = img.copy()
    if max(im.size) > MAX_SIDE:
        im.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    im.convert("RGB").save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def scenarios(base: Image.Image):
    W, H = base.size
    def variant(fn):
        im = base.convert("RGB").copy()
        fn(im)
        return im

    def s02(im):
        from PIL import ImageDraw
        d = ImageDraw.Draw(im)
        d.rectangle([0, 0, min(400, W), 40], fill=(20, 20, 20))
        d.text((10, 12), "OWNED BY SOMEBODY", fill=(255, 0, 0))

    def s03(im):
        from PIL import ImageDraw
        d = ImageDraw.Draw(im)
        d.rectangle([0, H // 2 - 30, W, H // 2 + 30], fill=(180, 0, 0))

    def s05(im):
        from PIL import ImageDraw
        d = ImageDraw.Draw(im)
        d.rectangle([W - 90, 0, W, 24], fill=(255, 255, 255))
        d.text((W - 85, 6), "12:34", fill=(0, 0, 0))

    def s07(im):
        from PIL import ImageDraw
        d = ImageDraw.Draw(im)
        d.rectangle([W // 4, H // 3, 3 * W // 4, 2 * H // 3], fill=(128, 128, 128))

    def s08(im):
        from PIL import ImageEnhance
        return ImageEnhance.Brightness(im).enhance(1.4)

    def s09(im):
        from PIL import ImageDraw
        d = ImageDraw.Draw(im)
        d.rectangle([0, H // 2, W, H], fill=(255, 255, 255))

    return [
        ("S01_identical", base),
        ("S02_text_swap", variant(s02)),
        ("S03_banner", variant(s03)),
        ("S04_blackout", Image.new("RGB", (W, H), (0, 0, 0))),
        ("S05_clock_noise", variant(s05)),
        ("S06_shift_2px", base.crop((2, 0, W, H)).resize((W, H))),
        ("S07_placeholder", variant(s07)),
        ("S08_wash", s08(base.convert("RGB").copy())),
        ("S09_half_white", variant(s09)),
        ("S10_identical_repeat", base),
    ]


def extract_score(text: str):
    m = re.search(r"(?<![\d.])(100|[0-9]{1,2})(?![\d.])", text or "")
    if not m:
        return None
    v = int(m.group(1))
    return v if 0 <= v <= 100 else None


def _band_pairs(h: int, tile_h: int = 1280, overlap: int = 64):
    """Horizontal bands covering [0,h); interior bands re-read `overlap` px so
    a defacement straddling a cut is fully visible in at least one band."""
    bands = []
    y = 0
    while y < h:
        y2 = min(h, y + tile_h)
        bands.append((max(0, y - overlap) if y > 0 else 0, y2))
        if y2 >= h:
            break
        y = y2
    return bands


def probe(endpoint: str, base: Image.Image, timeout: float = 180.0,
          tile: bool = False, only=None, tile_h: int = 1280):
    """Returns (per_scenario dict, fingerprint, model, box_error).

    tile=True (v3, S8-correct): score baseline/live in horizontal bands and
    aggregate MIN-wins (worst band is the page score). Never thumbnail-fit.
    Fail-loud aggregation: if ANY band failed to score, the page score is
    null — a page with an unscoreable band is not 'healthy'.
    """
    rows = {}
    fp = model = ""
    try:
        with httpx.Client(timeout=timeout) as c:
            for name, img in scenarios(base):
                if only and name not in only:
                    continue
                bands = _band_pairs(base.size[1], tile_h) if tile else [(0, base.size[1])]
                per_band = []
                for y0, y1 in bands:
                    b_img = base.crop((0, y0, base.size[0], y1)) if tile else base
                    l_img = img.crop((0, y0, img.size[0], y1)) if tile else img
                    payload = {
                        "messages": [
                            {"role": "system", "content": PROMPT_SYSTEM},
                            {"role": "user", "content": [
                                {"type": "text", "text": "Baseline:"},
                                {"type": "image_url", "image_url": {"url": data_url(b_img)}},
                                {"type": "text", "text": "Live:"},
                                {"type": "image_url", "image_url": {"url": data_url(l_img)}},
                                {"type": "text", "text": PROMPT_USER},
                            ]},
                        ],
                        # v2 (Sammy): constrained json_schema output + generous
                        # budget. This reasoning model spends ~800 tokens on
                        # reasoning_content before answering; 1600 leaves room.
                        "max_tokens": 1600,
                        "temperature": 0,
                        "response_format": RESPONSE_FORMAT,
                    }
                    r = c.post(endpoint, json=payload)
                    r.raise_for_status()
                    d = r.json()
                    fp = d.get("system_fingerprint", fp)
                    model = d.get("model", model)
                    ch = d["choices"][0]
                    finish = ch.get("finish_reason")
                    content = ch["message"].get("content") or ""
                    reasoning = ch["message"].get("reasoning_content") or ""
                    band = {"band": f"{y0}-{y1}", "finish_reason": finish,
                            "reasoning_tokens_approx": len(reasoning) // 4,
                            "raw": content[:300]}
                    if finish == "length":
                        # S6-class discipline inside the instrument: truncated
                        # output is a FAILED probe, never a parsed/default score.
                        band["score"] = None
                        band["error"] = "CHECK_FAILED: finish_reason=length"
                    else:
                        try:
                            obj = json.loads(content)
                            s = obj.get("score")
                            band["score"] = s if isinstance(s, int) and 0 <= s <= 100 else None
                            band["differences"] = obj.get("differences", [])
                            if band["score"] is None:
                                band["error"] = "CHECK_FAILED: score out of range/type"
                        except json.JSONDecodeError as e:
                            band["score"] = None
                            band["error"] = f"CHECK_FAILED: unparseable JSON ({e})"
                    per_band.append(band)
                valid = [b["score"] for b in per_band if b["score"] is not None]
                row = {"bands": per_band, "n_bands": len(per_band)}
                if len(valid) != len(per_band):
                    row["score"] = None
                    row["error"] = ("CHECK_FAILED: "
                                    + str(len(per_band) - len(valid)) + " band(s) failed")
                else:
                    worst = min(per_band, key=lambda b: b["score"])
                    row["score"] = worst["score"]
                    row["worst_band"] = worst["band"]
                    row["differences"] = worst.get("differences", [])
                rows[name] = row
    except Exception as e:
        return rows, fp, model, f"{type(e).__name__}: {e}"
    return rows, fp, model, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--endpoint", action="append", required=True,
                    metavar="NAME=URL", help="box label + /v1/chat/completions URL")
    ap.add_argument("--baseline", required=True, help="known-good PNG")
    ap.add_argument("--out", default="probe_results.json")
    ap.add_argument("--tile", action="store_true",
                    help="v3: band-tile tall pages (no thumbnail-fit), MIN-wins")
    ap.add_argument("--only", default="",
                    help="comma-separated scenario names to run (default: all)")
    a = ap.parse_args()

    endpoints = dict(e.split("=", 1) for e in a.endpoint)
    base = Image.open(a.baseline)
    only = {s for s in a.only.split(",") if s} or None
    names = [n for n, _ in scenarios(base)]
    if only:
        names = [n for n in names if n in only]

    results, any_err = {}, False
    for label, url in endpoints.items():
        rows, fp, model, err = probe(url, base, tile=a.tile, only=only)
        results[label] = {"url": url, "fingerprint": fp, "model": model,
                          "error": err, "scores": rows}
        if err:
            any_err = True
        print(f"[{label}] fingerprint={fp or '?'} model={model or '?'}"
              + (f" ERROR {err}" if err else " done"), file=sys.stderr)

    # table
    hdr = f"{'scenario':22}" + "".join(f"{lb:>12}" for lb in endpoints)
    print(hdr)
    print("-" * len(hdr))
    deltas = []
    labels = list(endpoints)
    for n in names:
        row = f"{n:22}"
        scores = []
        for lb in labels:
            s = results[lb]["scores"].get(n, {}).get("score")
            scores.append(s)
            row += f"{('' if s is None else s):>12}"
        valid = [s for s in scores if s is not None]
        if len(valid) >= 2:
            d = max(valid) - min(valid)
            deltas.append((n, d))
            row += f"   Δ={d}"
        print(row)

    det = [results[labels[0]]["scores"][n]["score"] for n in ("S01_identical", "S10_identical_repeat")
           if n in results[labels[0]]["scores"]]
    print(f"\nintra-box determinism (S01 vs S10, {labels[0]}): {det}")
    if deltas:
        mean_d = statistics.mean(d for _, d in deltas)
        worst = max(deltas, key=lambda x: x[1])
        print(f"cross-box Δ mean={mean_d:.1f} worst={worst[0]}:{worst[1]}")
        print("VERDICT: " + ("Δ≈0 -> fan-out SAFE" if mean_d <= 3 and worst[1] <= 10
              else "Δ nonzero -> stratify by box or pin builds before calibration"))

    with open(a.out, "w") as f:
        json.dump({"baseline_sha256": hashlib.sha256(open(a.baseline, "rb").read()).hexdigest(),
                   "results": results, "deltas": dict(deltas)}, f, indent=2)
    print(f"raw -> {a.out}")
    sys.exit(2 if any_err else 0)


if __name__ == "__main__":
    main()

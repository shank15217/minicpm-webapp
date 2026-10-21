#!/usr/bin/env python3
"""anchor_mini_cal.py — §2 mini-calibration harness for anchorcheck (v0.5.5 gate).

New prompt territory is guilty until characterized (Act 6). Before the
content-anchor checklist is allowed anywhere near an alert, this harness
characterizes it on cells:

  real      — actual baseline band crop (facts expected: whatever holds)
  blank     — band painted white: model MUST report is_blank / drop text &
              branding (laundering cell: a "has_text=true, branding=true"
              on a blank image is the blanked-crop-scored-100 disease in
              checklist clothing -> anchor REJECTED)
  banner    — 1.3%-area overlay (the defacement size the pixel channel is
              blind to on fhlb band 0): facts should still read true, the
              anchor is a coarse net, this cell records its actual floor
  halfwhite — bottom-half blanked (structural damage cell)

Per cell x prompt-variant x tower x N reps: report per-fact rates.
Acceptance (printed as PASS/FAIL per rule):
  A1 blank-cell honesty: is_blank true-rate >= 0.9 AND has_text true-rate
     <= 0.1 (the model may not invent content on emptiness)
  A2 variant stability: for real cells, |rateA - rateB| <= 0.2 per fact
     (wording must not flip facts)
  A3 determinism sanity: temperature 0; runs identical-input reproducible
     at >= 0.8 per-fact agreement across reps

Usage (on the tower host): python scripts/anchor_mini_cal.py <baseline.png>
       [--band 0] [--reps 3] [--towers http://127.0.0.1:8080,http://127.0.0.1:8081]
"""
from __future__ import annotations
import argparse, base64, io, json, sys, urllib.request
from collections import defaultdict
from PIL import Image, ImageDraw

sys.path.insert(0, ".")
from anchorcheck import ANCHOR_PROMPT, CHECKLIST_SCHEMA, parse_facts, check_facts

PROMPT_B = ANCHOR_PROMPT + " Answer strictly from THIS image alone; if unsure, answer false."


def data_url(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def variants(img: Image.Image) -> dict:
    w, h = img.size
    out = {"real": img}
    blank = Image.new("RGB", (w, h), "white")
    out["blank"] = blank
    banner = img.copy()
    bh = max(24, int(0.013 * h))   # 1.3% of band area: full width x bh
    ImageDraw.Draw(banner).rectangle([0, int(h * 0.35), w, int(h * 0.35) + bh],
                                     fill=(200, 0, 0))
    out["banner"] = banner
    half = img.copy()
    ImageDraw.Draw(half).rectangle([0, h // 2, w, h], fill="white")
    out["halfwhite"] = half
    return out


def ask(tower: str, img: Image.Image, prompt: str) -> dict:
    payload = {
        "messages": [
            {"role": "system", "content": "You answer narrow factual questions about images, nothing else."},
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": data_url(img)}},
                {"type": "text", "text": prompt},
            ]},
        ],
        "max_tokens": 400, "temperature": 0,
        "response_format": CHECKLIST_SCHEMA,
    }
    req = urllib.request.Request(tower.rstrip("/") + "/v1/chat/completions",
                                 data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        d = json.loads(r.read())
    ch = d["choices"][0]
    f = parse_facts(ch["message"].get("content") or "")
    f["finish"] = ch.get("finish_reason")
    return f


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("png")
    ap.add_argument("--band", type=int, default=0)
    ap.add_argument("--band-h", type=int, default=1280)
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--towers", default="http://127.0.0.1:8080")
    args = ap.parse_args()

    im = Image.open(args.png)
    y0 = args.band * args.band_h
    crop = im.crop((0, y0, im.size[0], min(im.size[1], y0 + args.band_h + 64)))
    cells = variants(crop)
    towers = args.towers.split(",")
    # expected facts = facts observed on the REAL cell at rep 0 (profile stub;
    # real v0.5.5 builds expected from attested control runs)
    stats = defaultdict(list)   # (tower, variant, prompt, fact) -> [bool]
    errors = []
    for tower in towers:
        for vname, vimg in cells.items():
            for pname, prompt in (("A", ANCHOR_PROMPT), ("B", PROMPT_B)):
                for rep in range(args.reps):
                    f = ask(tower, vimg, prompt)
                    if not f.get("ok"):
                        errors.append((tower, vname, pname, rep, f.get("error") or f.get("finish")))
                        continue
                    for k in ("has_text", "has_branding", "has_navigation", "is_blank"):
                        stats[(tower, vname, pname, k)].append(f[k])
                    print(f"  {tower.rsplit(':',3)[-1]} {vname:9s} {pname} rep{rep}: "
                          f"text={f['has_text']} brand={f['has_branding']} nav={f['has_navigation']} "
                          f"blank={f['is_blank']} sample={f['text_sample'][:60]!r}", flush=True)

    def rate(t, v, p, k):
        xs = stats[(t, v, p, k)]
        return (sum(xs) / len(xs)) if xs else float("nan")

    print("\n== acceptance ==")
    fails = 0
    for t in towers:
        for p in "AB":
            ib, ht = rate(t, "blank", p, "is_blank"), rate(t, "blank", p, "has_text")
            ok = ib >= 0.9 and ht <= 0.1
            fails += 0 if ok else 1
            print(f"  A1 blank-honesty {t} p{p}: is_blank={ib:.2f} has_text={ht:.2f} -> {'PASS' if ok else 'FAIL'}")
        for k in ("has_text", "has_branding", "has_navigation"):
            d = abs(rate(t, "real", "A", k) - rate(t, "real", "B", k))
            ok = d <= 0.2
            fails += 0 if ok else 1
            print(f"  A2 stability {t} {k}: |A-B|={d:.2f} -> {'PASS' if ok else 'FAIL'}")
        for k in ("has_text", "has_branding"):
            ra = stats[(t, "real", "A", k)]
            agree = max(sum(ra), len(ra) - sum(ra)) / len(ra) if ra else float("nan")
            ok = agree >= 0.8
            fails += 0 if ok else 1
            print(f"  A3 determinism {t} {k}: rep-agreement={agree:.2f} -> {'PASS' if ok else 'FAIL'}")
    if errors:
        print(f"  INSTRUMENT ERRORS: {errors}")
        fails += 1
    print(f"\n== mini-cal {'GREEN' if fails == 0 else f'RED ({fails} failed cells)'} — "
          f"anchor mode may {'NOT' if fails else ''} feed alerts"
          + (" (banner/floor cells recorded, human reads rates)" if fails == 0 else ""))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()

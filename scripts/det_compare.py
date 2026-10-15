#!/usr/bin/env python3
"""det_compare.py — deterministic band comparison (v0.6 verdict layer, prototype).

pHash(dHash-64) + normalized MAE per horizontal band, MIN-wins page score.
No prompts, no VLM, no confabulation: identical input scores 1.0 every time.
This is the layer that OWNS 'did anything change'; the VLM only narrates.

Usage:
  python scripts/det_compare.py --baseline <png> [--deface half-white|band5|overlay]
Compares baseline vs (a) itself (control) and (b) synthetic variants, prints
per-band similarity table. stdlib + Pillow only.
"""
import argparse
from PIL import Image, ImageDraw, ImageFont

BAND = 1280
OVER = 64


def bands(h):
    out, y = [], 0
    while y < h:
        y2 = min(h, y + BAND)
        out.append((max(0, y - OVER) if y > 0 else 0, y2))
        if y2 >= h:
            break
        y = y2
    return out


def dhash(img, size=8):
    g = img.convert("L").resize((size + 1, size), Image.Resampling.LANCZOS)
    px = list(g.getdata())
    bits = 0
    for r in range(size):
        for c in range(size):
            bits = (bits << 1) | (1 if px[r * (size + 1) + c] > px[r * (size + 1) + c + 1] else 0)
    return bits


def hamming_sim(a, b):
    return 1.0 - bin(a ^ b).count("1") / 64.0


def mae_sim(a, b):
    import itertools
    ga, gb = a.convert("L").resize((256, 256), Image.Resampling.LANCZOS), b.convert("L").resize((256, 256), Image.Resampling.LANCZOS)
    d = sum(abs(x - y) for x, y in zip(ga.getdata(), gb.getdata())) / (256 * 256)
    return 1.0 - d / 255.0


def band_sim(ia, ib):
    a, b = ia.crop((0, 0, ia.size[0], min(ia.size[1], 6000))), ib.crop((0, 0, ib.size[0], min(ib.size[1], 6000)))
    hs = hamming_sim(dhash(a), dhash(b))
    ms = mae_sim(a, b)
    return min(hs, ms), hs, ms


def variants(base):
    W, H = base.size
    out = {"control_identical": base.copy()}
    hw = base.copy()
    d = ImageDraw.Draw(hw)
    d.rectangle([0, H // 2, W, H], fill=(255, 255, 255))
    out["half_white"] = hw
    b5 = base.copy()
    d = ImageDraw.Draw(b5)
    y0 = min(5 * BAND, H - 1)
    d.rectangle([0, y0, W, H], fill=(255, 255, 255))
    out["band5_blank"] = b5
    ov = base.copy()
    d = ImageDraw.Draw(ov)
    d.rectangle([0, H - 400, W, H - 300], fill=(180, 0, 0))
    try:
        f = ImageFont.truetype("/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf", 48)
    except Exception:
        f = ImageFont.load_default()
    d.text((20, H - 390), "QA BANNER", fill=(255, 255, 255), font=f)
    out["banner_100px"] = ov
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", required=True)
    a = ap.parse_args()
    base = Image.open(a.baseline).convert("RGB")
    H = base.size[1]
    print(f"baseline {base.size}, {len(bands(H))} bands; per-band page similarity = MIN over bands")
    print(f"{'variant':20}{'page_sim':>10}  per-band sims")
    for name, var in variants(base).items():
        row = []
        for y0, y1 in bands(H):
            ba = base.crop((0, y0, base.size[0], y1))
            bb = var.crop((0, y0, var.size[0], y1))
            s, hs, ms = band_sim(ba, bb)
            row.append(round(s, 3))
        page = min(row)
        verdict = "CHANGED" if page < 0.995 else "identical"
        print(f"{name:20}{page:>10.3f}  {row}  -> {verdict}")


if __name__ == "__main__":
    main()

"""detdiff — deterministic band comparison: the layer that owns WHETHER.

QA-signed architecture (QA-REPORT-5, Acts 1-6): a 1B VLM pairwise
same-or-different judgment is prompt-pragmatics-corrupted (strict wording
confabulates differences on identical pages; permissive wording launders
blanked pages to score 100). Pixel comparison neither confabulates nor
obeys prompts: identical input scores exactly 1.0, every time, with no GPU.

So: this module answers *did anything change* (deterministic, always run);
the VLM is demoted to narrating regions the diff already located, and is
skipped entirely when nothing changed — which also kills the alert-flood
and most of the inference cost.

Per-band dHash-64 (structural) combined with normalized MAE on a 256px
downsample (luminance shift), per band: sim = min(hash_sim, mae_sim).
Page score = MIN over bands (worst band is the page). Overlap between
bands so a defacement straddling a cut is fully visible in one band.

Threshold: DET_PASS_SIM env, default 0.995. That number is a PLACEHOLDER
pending the v0.5 calibration control runs (per-baseline, per-band control
profiles — Sammy/Quinn sign-off conditions); what is proven at commit time
is ordering and determinism, not the cut-point: identical 1.000,
half-white 0.259, band-blank 0.662, 100px banner 0.969 on the production
fhlb baseline.
"""
from __future__ import annotations

import io
from PIL import Image

BAND_H = 1280
BAND_OVERLAP = 64


def bands(h: int) -> list[tuple[int, int]]:
    """Horizontal (y0, y1) bands covering [0, h) with interior overlap."""
    out: list[tuple[int, int]] = []
    y = 0
    while y < h:
        y2 = min(h, y + BAND_H)
        out.append((max(0, y - BAND_OVERLAP) if y > 0 else 0, y2))
        if y2 >= h:
            break
        y = y2
    return out


def dhash(img: Image.Image, size: int = 8) -> int:
    """64-bit difference hash on an (size+1)xsize gradient resize."""
    g = img.convert("L").resize((size + 1, size), Image.Resampling.LANCZOS)
    px = g.tobytes()  # L-mode: one byte per pixel, row-major
    bits = 0
    for r in range(size):
        for c in range(size):
            bits = (bits << 1) | (
                1 if px[r * (size + 1) + c] > px[r * (size + 1) + c + 1] else 0
            )
    return bits


def hamming_sim(a: int, b: int) -> float:
    return 1.0 - bin(a ^ b).count("1") / 64.0


def mae_sim(a: Image.Image, b: Image.Image, sample: int = 256) -> float:
    """1 - mean-abs-error/255 on a fixed-size grayscale downsample."""
    ga = a.convert("L").resize((sample, sample), Image.Resampling.LANCZOS).tobytes()
    gb = b.convert("L").resize((sample, sample), Image.Resampling.LANCZOS).tobytes()
    d = sum(abs(x - y) for x, y in zip(ga, gb)) / (sample * sample)
    return 1.0 - d / 255.0


def _open(image_bytes: bytes) -> Image.Image:
    return Image.open(io.BytesIO(image_bytes)).convert("RGB")


def page_diff(baseline_bytes: bytes, live_bytes: bytes,
              threshold: float = 0.995) -> dict:
    """Deterministic change verdict between two page screenshots.

    Returns {page_sim, changed: bool, threshold, band_sims, changed_bands,
    n_bands}. changed_bands lists "y0-y1" windows with sim < threshold —
    the exact regions the narration VLM (and any human alert) should look
    at. Pure CPU, deterministic: same inputs -> same numbers, always.
    """
    a = _open(baseline_bytes)
    b = _open(live_bytes)
    # Height mismatch is itself a change (page grew/shrank/collapsed).
    h = min(a.size[1], b.size[1])
    band_sims: list[float] = []
    changed_bands: list[str] = []
    for y0, y1 in bands(h):
        ba = a.crop((0, y0, a.size[0], y1))
        bb = b.crop((0, y0, b.size[0], y1))
        hs = hamming_sim(dhash(ba), dhash(bb))
        ms = mae_sim(ba, bb)
        s = min(hs, ms)
        band_sims.append(round(s, 4))
        if s < threshold:
            changed_bands.append(f"{y0}-{y1}")
    page_sim = min(band_sims) if band_sims else 1.0
    changed = bool(changed_bands) or abs(a.size[1] - b.size[1]) > 2 * BAND_OVERLAP
    # (b)-lite aligned rematch (calibration-protocol 6.5, Paul's ruling:
    # (a)+(b)-lite): for FLAGGED bands only, re-compare the live band window
    # against baseline windows at +/- one band offset and keep the BEST sim.
    # Sub-flag content insertion shifts every downstream band; without this,
    # changed_bands over-reports "everything below the insertion" and the
    # narrator crops point at innocent pixels.
    # HARD GUARDRAIL (QA, non-negotiable): aligned sims NEVER feed `changed`
    # or `page_sim` above — the alert consumes the RAW page-min only.
    # "Keep the best sim" is a maximization direction and every laundering
    # story in this project started with maximizing agreement; aligned sims
    # are admitted solely for localization, narrator-crop selection, and
    # tolerance distributions (calibration 2 stores both channels).
    aligned_band_sims = list(band_sims)
    align_offsets = [0] * len(band_sims)
    band_list = bands(h)
    # Search radius per Sammy's spec: sub-flag drift is <= 2*BAND_OVERLAP px,
    # so scan that range on a 32px grid. (A first cut used +/-BAND_H offsets;
    # the synthetic-insertion test showed a 100px shift is aligned by NO whole
    # band offset — the grid has to match the actual drift scale.)
    ALIGN_SEARCH = [o for o in range(-2 * BAND_OVERLAP, 2 * BAND_OVERLAP + 1, 32) if o]
    for i, (y0, y1) in enumerate(band_list):
        if band_sims[i] >= threshold and abs(a.size[1] - b.size[1]) <= 2 * BAND_OVERLAP:
            continue  # unflagged: zero extra work by design
        bb = b.crop((0, y0, b.size[0], y1))
        best, best_off = band_sims[i], 0
        for off in ALIGN_SEARCH:
            ay0, ay1 = max(0, y0 + off), min(a.size[1], y1 + off)
            if ay1 - ay0 < (y1 - y0) // 2:
                continue  # window mostly off-page: not a valid rematch
            ba = a.crop((0, ay0, a.size[0], ay1))
            s = min(hamming_sim(dhash(ba), dhash(bb)), mae_sim(ba, bb))
            if s > best:
                best, best_off = round(s, 4), off
        aligned_band_sims[i] = round(best, 4)
        align_offsets[i] = best_off
    return {
        "page_sim": round(page_sim, 4),
        "changed": changed,
        "threshold": threshold,
        "band_sims": band_sims,
        "aligned_band_sims": aligned_band_sims,
        "band_align_offsets": align_offsets,
        "changed_bands": changed_bands,
        "n_bands": len(band_sims),
        "height_delta_px": b.size[1] - a.size[1],
        "algorithm": "dhash64+mae256/min-per-band, min-over-bands",
    }

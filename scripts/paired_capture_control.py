#!/usr/bin/env python3
"""Paired-capture control + plateau-series extractor (Sammy/Quinn, 2026-10-25).

Two modes, one question — is a site's hourly similarity jitter the CAPTURE
or the WORLD?

  --series   offline: read the control ledger, print the per-site page_sim
             plateau histogram (discrete plateau sets are the fingerprint of
             capture-time phase — carousels, lazy-load, regime interaction).
  --captures live: N paired full-page captures with and without the app's
             own LOADER_JS ritual (imported verbatim from app.py — regime
             parity by single source), each scored against the pinned
             baseline with detdiff.page_diff, plus a blank-tail probe per
             shot. with-prescroll variance + no-prescroll flatness locates
             the churn source; a FLAT no-prescroll series is NOT a pass on
             its own — read the changed-band list: flat-but-absent-content
             means the regime flip is a re-baseline ceremony, not a flip
             (careers lesson: 0.953 flat, four bands permanently changed).

Measured precedent (careers, 2026-10-25, six shots):
    with-prescroll  0.8594 / 0.7969 / 0.8125  (three store-plateau values)
    no-prescroll    0.9531 / 0.9531 / 0.9531  (flat; bands 0,2496,3776,5056)
    blank_tail      False x6  — six shots cannot retire the Oct-19 blank-
    tail class; the regime decision is multi-day evidence, not one table.

Run from the repo root in the prod venv (needs playwright + browser):
    .venv/bin/python scripts/paired_capture_control.py --series
    .venv/bin/python scripts/paired_capture_control.py --key b7d1b72d27ed4f91 -n 3
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import pathlib
import sys
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import detdiff  # noqa: E402


def series(store: pathlib.Path) -> None:
    by = {}
    for line in store.read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("page_sim") is not None:
            by.setdefault(r["baseline_key"], []).append(
                (r["ts"], r["page_sim"]))
    for key, rows in sorted(by.items()):
        sims = [s for _, s in rows]
        hist = Counter(round(s, 4) for s in sims)
        print(f"{key}  n={len(sims)} min={min(sims):.4f} max={max(sims):.4f} "
              f"distinct={len(hist)}")
        for value, count in hist.most_common(6):
            print(f"    plateau {value}: {count} fires")


async def captures(key: str, n: int, url_override: str | None) -> None:
    import app as A  # prod venv only: needs playwright
    from PIL import Image
    from playwright.async_api import async_playwright

    idx = json.loads(pathlib.Path("baselines/index.json").read_text())["baselines"]
    entry = idx[key]
    url = url_override or entry["url"]
    base = pathlib.Path("baselines") / f"{key}.png"
    base_bytes = base.read_bytes()

    async def grab(pw, prescroll: bool) -> tuple[int, bool, dict]:
        browser = await A._get_browser()
        ctx = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/131.0.0.0 Safari/537.36"),
            locale="en-US")
        await ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        page = await ctx.new_page()
        await page.route("**/*", A._ssrf_playwright_route)
        await page.goto(url, wait_until="load", timeout=60000)
        await page.wait_for_timeout(2500)
        if prescroll:
            try:
                await page.evaluate(A.LOADER_JS)
                await page.wait_for_function(
                    "() => Array.from(document.images).every(i => i.complete)",
                    timeout=20000)
            except Exception:
                pass  # best-effort, same as production path
        await page.wait_for_timeout(600)
        png = await page.screenshot(full_page=True, type="png")
        await ctx.close()
        im = Image.open(io.BytesIO(png)).convert("L")
        w, h = im.size
        blank_tail = im.crop((0, max(0, h - 400), w, h)).getextrema()[0] >= 250
        return h, blank_tail, detdiff.page_diff(base_bytes, png)

    async with async_playwright() as pw:
        for i in range(n):
            for tag, presc in (("with-prescroll", True), ("no-prescroll", False)):
                h, blank, d = await grab(pw, presc)
                bs = sorted(d.get("band_sims") or [])
                med = bs[len(bs) // 2] if bs else float("nan")
                print(f"{tag:14s} h={h} blank_tail={blank} "
                      f"page_sim={d['page_sim']:.4f} band_med={med:.4f} "
                      f"changed_bands={d['changed_bands']}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--series", action="store_true",
                    help="print per-site page_sim plateau histogram")
    ap.add_argument("--store", default="/root/mpw-calibration/control.jsonl")
    ap.add_argument("--key", help="baseline key to paired-capture")
    ap.add_argument("--url", default=None, help="override URL for the capture")
    ap.add_argument("-n", type=int, default=3, help="pairs per regime")
    args = ap.parse_args()
    if args.series:
        series(pathlib.Path(args.store))
    elif args.key:
        asyncio.run(captures(args.key, args.n, args.url))
    else:
        ap.error("choose --series or --key")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

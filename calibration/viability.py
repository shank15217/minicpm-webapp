#!/usr/bin/env python3
"""Det-only viability entries (user go 2026-10-21, five-site expansion).

Mirrors rig.py's capture flow EXACTLY (same imports, UA, viewport,
route guard, prescroll-4 sequence, block_mean_grid) but for the five
candidate URLs — and NOTHING else: writes its own ledger
(/root/mpw-calibration/viability.jsonl), never touches control.jsonl,
never calls the VLM, never reads or writes a baseline. The production
cohort's persistence windows partition by baseline_key and these rows
carry their own "viab:<name>" keys; two ledgers make the separation
structural, not just key-level.

Purpose: capture hourly until each candidate has >= 24 mean-grid fires;
detdiff.viability_report() then graduates sites from the guard's own
output (mode_share >= 23/24, regime-set equality, blank share) and
reports each site's content noise floor — waiver/oscillator lines write
themselves from that report, not from memory. Consumer law (Quinn): the
only truthy decision field in the report is `graduates_shape`.

A failed capture writes an error row WITHOUT a mean_grid — counts as a
fire, not as a mean_grid fire; no fabricated grids.
"""
import asyncio
import datetime
import json
import os
import sys
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# @user-approved candidates 2026-10-21, one role each:
CANDIDATES = [
    {"name": "kernel", "url": "https://www.kernel.org/"},        # static control
    {"name": "litcnn", "url": "https://lite.cnn.com/"},          # slow-edit text
    {"name": "hnrack", "url": "https://news.ycombinator.com/"},  # ranked churn
    {"name": "applhero", "url": "https://www.apple.com/"},       # hero rotator
    {"name": "wikport", "url": "https://www.wikipedia.org/"},    # quasi-static
]

# S2 gate parity: the default-deny allowlist refused all five documents on
# the first smoke fire (honest aborts, error rows, no grids — observed live).
# The viability list IS this runner's operator-approved target set, so the
# process-scoped env is derived from CANDIDATES itself: nothing approved
# here is reachable that isn't on the list, every check_target protection
# (verbatim hosts, metadata-class refusal, rebinding scan) still runs, and
# the production rig/app keep their own default untouched.
os.environ["MPW_ALLOW_TARGETS"] = ",".join(
    urlparse(c["url"]).hostname for c in CANDIDATES)

import rig  # import-safe (main guarded); reuses its capture constants
import detdiff
from playwright.async_api import async_playwright

LEDGER = os.environ.get("MPW_VIABILITY_LEDGER",
                        "/root/mpw-calibration/viability.jsonl")


def _ts() -> str:
    return (datetime.datetime.now(datetime.timezone.utc)
            .isoformat().replace("+00:00", "Z"))


async def main() -> int:
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    rows = []
    async with async_playwright() as p:
        b = await p.chromium.launch(args=["--disable-blink-features=AutomationControlled",
                                          "--no-sandbox", "--disable-dev-shm-usage"])
        ctx = await b.new_context(viewport={"width": 1280, "height": 900},
                                  user_agent=rig.UA if hasattr(rig, "UA") else
                                  ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                                   "Chrome/131.0.0.0 Safari/537.36"),
                                  locale="en-US")
        await ctx.route("**/*", rig._guard)
        await ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        for c in CANDIDATES:
            row = {"ts": _ts(),
                   "window": datetime.datetime.now().strftime("autoT%H"),
                   "trigger": "auto", "kind": "viability",
                   "baseline_key": f"viab:{c['name']}", "url": c["url"],
                   "verdict": "viability_det_only",
                   "capture_regime": rig.CAPTURE_REGIME,
                   "capture_surface": rig._surface(),
                   "app_commit": rig.APP_COMMIT}
            page = await ctx.new_page()
            try:
                await page.goto(c["url"], wait_until="load", timeout=60000)
                await page.wait_for_timeout(2500)
                try:  # prescroll-4 parity with production rig
                    await page.evaluate(rig._LOADER)
                    await page.wait_for_function(
                        "() => Array.from(document.images).every(i => i.complete)",
                        timeout=20000)
                except Exception:
                    pass
                await page.wait_for_timeout(600)
                shot = await page.screenshot(full_page=True, type="png")
                row.update({
                    "page_bytes": len(shot),
                    "capture_sanity": json.dumps(
                        {"blank_bands": rig._blank_bands(shot)}),
                    "mean_grid": json.dumps(detdiff.block_mean_grid(shot)),
                })
            except Exception as exc:  # noqa: BLE001 — error row, never a grid
                row["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
            finally:
                await page.close()
            rows.append(row)
        await b.close()
    with open(LEDGER, "a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    ok = sum(1 for r in rows if "mean_grid" in r)
    errs = [r["baseline_key"] for r in rows if "error" in r]
    print(f"{_ts()} viability fire: {len(rows)} entries, "
          f"{ok} mean grids, errors={errs}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

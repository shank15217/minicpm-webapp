"""Control-run batch (calibration protocol v2 §2). Mirrors app.py capture
exactly: viewport 1280x900, wait_until=load, 2500ms settle, full_page PNG.
Det-only (no VLM). Appends JSONL rows; window label via argv."""
import asyncio, hashlib, json, pathlib, sys, time, datetime
from pathlib import Path
import os
REPO = pathlib.Path(__file__).resolve().parent.parent  # repo copy works anywhere
sys.path.insert(0, str(REPO))
sys.path.insert(0, os.environ.get("MPW_REPO", "/root/minicpm-webapp"))
os.chdir(str(REPO))  # app.py mounts static/ relative to CWD at import; cron cwd differs
from app import (_ssrf_playwright_route as _guard, LOADER_JS as _LOADER,
                blank_tail_bands as _blank_bands,
                capture_surface_sha as _surface)  # SAME capture surface as serving app
import detdiff
from playwright.async_api import async_playwright

PROD = Path(os.environ.get("MPW_BASELINES", "/root/minicpm-webapp/baselines"))
OUT = Path(os.environ.get("MPW_CONTROL_ROWS", "/root/mpw-calibration/control.jsonl"))
win = sys.argv[1] if len(sys.argv) > 1 else datetime.datetime.now().strftime("autoT%H")
TRIGGER = "manual" if len(sys.argv) > 1 else "auto"
import subprocess
CAPTURE_REGIME = "prescroll-4"  # +UA parity +route-guard parity (third-party state deterministic)
def _app_commit():
    try:
        return subprocess.run(["git","-C","/root/minicpm-webapp","rev-parse","--short","HEAD"],
                              capture_output=True,text=True,timeout=5).stdout.strip() or "unknown"
    except Exception:
        return "unknown"
APP_COMMIT = _app_commit()  # structured provenance (Sammy): manual rows must never silently weight diurnal stats

async def main():
    idx = json.loads((PROD / "index.json").read_text())
    UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
    async with async_playwright() as p:
        b = await p.chromium.launch(args=["--disable-blink-features=AutomationControlled",
                                          "--no-sandbox", "--disable-dev-shm-usage"])
        ctx = await b.new_context(viewport={"width": 1280, "height": 900},
                                  user_agent=UA, locale="en-US")
        await ctx.route("**/*", _guard)
        await ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        for key, meta in idx["baselines"].items():
            url = meta.get("url"); png = PROD / f"{key}.png"
            if not url or not png.exists():
                print(f"skip {key}"); continue
            page = await ctx.new_page()
            try:
                await page.goto(url, wait_until="load", timeout=60000)
                await page.wait_for_timeout(2500)
                # prescroll-1: mirror app.py ab90c5d capture regime
                # (lazy-loader pre-scroll) so controls and checks measure
                # the SAME page state distribution (regime parity).
                try:
                    await page.evaluate(_LOADER)
                    await page.wait_for_function(
                        "() => Array.from(document.images).every(i => i.complete)",
                        timeout=20000)
                except Exception:
                    pass
                await page.wait_for_timeout(600)
                shot = await page.screenshot(full_page=True, type="png")
                d = detdiff.page_diff(png.read_bytes(), shot, threshold=0.995)
                try:  # shadow metric (spatial-ceiling fit); never breaks batch
                    _bm = json.dumps(detdiff.block_mae_shadow(png.read_bytes(), shot))
                except Exception:
                    _bm = None
                row = {"ts": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00","Z"),
                       "window": win, "trigger": TRIGGER, "kind": "control", "baseline_key": key, "url": url,
                       "baseline_sha": hashlib.sha256(png.read_bytes()).hexdigest(),
                       "detdiff_sha": hashlib.sha256(Path(detdiff.__file__).read_bytes()).hexdigest()[:16],
                       "capture_regime": CAPTURE_REGIME, "app_commit": APP_COMMIT,
                       "capture_sanity": json.dumps({"blank_bands": _blank_bands(shot)}),
                       "blockmae": _bm,
                       "capture_surface": _surface(),
                       **d}
                with OUT.open("a") as f: f.write(json.dumps(row) + "\n")
                print(key, "page_sim=", d["page_sim"], "changed=", d["changed"], "dh=", d["height_delta_px"])
            except Exception as e:
                print(key, "ERROR", repr(e)[:120])
            finally:
                await page.close()
        await b.close()

if __name__ == "__main__":  # import-safe (Oct-20 incident: bare import ran the whole batch)
    asyncio.run(main())

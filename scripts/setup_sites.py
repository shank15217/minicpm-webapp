#!/usr/bin/env python3
"""setup_sites — the clone-and-run entry point (user request 2026-10-25):

    uv run scripts/setup_sites.py --config monitor.toml

Reads [towers] + [[sites]] from monitor.toml, PROVES the towers (model id,
size, ctx via /v1/models + llama.cpp /props — model info and size gathered
AS PART OF collector setup, never folklore), captures one reference image
per URL with Playwright, asks the detail tower for a one-sentence
description of each discovery, and writes baselines/index.json plus a
human-readable state.toml. Idempotent: sites whose baseline exists and
whose url matches are kept unless --recapture.

Daily monitoring afterwards is scripts/run_daily.sh (capture+analyze via
the app service, then refresh_state.py rewrites the [state] block).
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

try:
    import tomllib
except ModuleNotFoundError:
    print("needs python>=3.11 (tomllib)", file=sys.stderr)
    raise SystemExit(2)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _get_json(url: str, timeout: int = 15):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


def probe_tower(name: str, base: str) -> dict:
    """Model info+size at setup time (user law): /v1/models for identity,
    llama.cpp /props for what it is; failure is LOUD, never an empty row."""
    base = base.rstrip("/")
    out = {"url": base}
    models = _get_json(f"{base}/v1/models").get("data", [])
    if not models:
        raise RuntimeError(f"tower {name}: /v1/models returned no models")
    out["model_id"] = models[0].get("id", "?")
    out["n_models"] = len(models)
    try:
        props = _get_json(f"{base}/props")
        out["model_path"] = props.get("model_path") or props.get("model_filename")
        out["n_ctx"] = props.get("n_ctx")
        out["total_slots"] = props.get("total_slots")
    except Exception:  # noqa: BLE001 — /props is llama.cpp-specific
        out["props"] = "unavailable (not llama.cpp?)"
    return out


def capture(url: str, out_png: str) -> tuple[int, int]:
    from playwright.async_api import async_playwright

    async def _shot():
        async with async_playwright() as p:
            b = await p.chromium.launch(args=["--no-sandbox"])
            page = await b.new_page(viewport={"width": 1280, "height": 800})
            try:
                await page.goto(url, wait_until="networkidle", timeout=45000)
                await page.wait_for_timeout(2500)
                await page.screenshot(path=out_png, full_page=True)
                h = await page.evaluate("document.body.scrollHeight")
            finally:
                await b.close()
        return h

    height = asyncio.run(_shot())
    from PIL import Image
    w, hh = Image.open(out_png).size
    return w, hh


def describe(png_path: str, detail_base: str) -> str:
    import base64
    import io
    from PIL import Image
    im = Image.open(png_path)
    im.thumbnail((1280, 2560))
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "PNG")
    body = json.dumps({"model": "detail", "temperature": 0, "max_tokens": 120,
                       "messages": [{"role": "user", "content": [
        {"type": "text", "text": "Describe this webpage screenshot in one "
                                 "factual sentence: site type and main content."},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,"
         + base64.b64encode(buf.getvalue()).decode()}}]}]}).encode()
    rq = urllib.request.Request(detail_base.rstrip("/") + "/v1/chat/completions",
                                data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(rq, timeout=180) as r:
        return json.loads(r.read())["choices"][0]["message"]["content"].strip()[:300]


def toml_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", default=os.path.join(ROOT, "monitor.toml"))
    ap.add_argument("--recapture", action="store_true")
    a = ap.parse_args()
    cfg = tomllib.load(open(a.config, "rb"))
    towers = cfg.get("towers", {})
    if not towers.get("detail"):
        print("monitor.toml needs [towers] detail = http://...:8081/v1", file=sys.stderr)
        return 2
    print("== collector: tower provenance ==")
    tower_info = {}
    for name in ("detail", "vision"):
        u = towers.get(name)
        if not u:
            continue
        base = u.rsplit("/v1", 1)[0]
        try:
            info = probe_tower(name, base)
        except Exception as e:  # noqa: BLE001
            print(f"tower {name}: UNREACHABLE {e}", file=sys.stderr)
            return 3
        tower_info[name] = info
        print(f"  {name}: {info['model_id']}  path={info.get('model_path', '?')}  "
              f"ctx={info.get('n_ctx', '?')}")

    idx_p = os.path.join(ROOT, "baselines", "index.json")
    idx = json.load(open(idx_p)) if os.path.exists(idx_p) else {"baselines": {}}
    print("== sites ==")
    for site in cfg.get("sites", []):
        url, label = site["url"], site.get("name") or url
        key = hashlib.sha256(url.encode()).hexdigest()[:16]
        png = os.path.join(ROOT, "baselines", f"{key}.png")
        entry = idx["baselines"].get(key, {})
        if os.path.exists(png) and entry.get("url") == url and not a.recapture:
            print(f"  {label}: baseline exists, kept ({key})")
        else:
            w, h = capture(url, png)
            sha = hashlib.sha256(open(png, "rb").read()).hexdigest()
            desc = ""
            try:
                desc = describe(png, towers["detail"])
            except Exception as e:  # noqa: BLE001
                desc = f"(description unavailable: {type(e).__name__})"
            idx["baselines"][key] = {
                "url": url, "path": f"baselines/{key}.png",
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "width": w, "height": h, "sha256": sha, "description": desc,
            }
            print(f"  {label}: captured {w}x{h}  {desc[:80]}")
        os.makedirs(os.path.dirname(idx_p), exist_ok=True)
        json.dump(idx, open(idx_p, "w"), indent=1)

    st = ["# state.toml — written by scripts/setup_sites.py; metrics refreshed",
          "# daily by scripts/refresh_state.py. Humans read this; machines",
          "# read baselines/index.json and runs.db.", "", "[collector]"]
    st.append(f'updated = "{datetime.now(timezone.utc).isoformat(timespec="seconds")}"')
    for name, info in tower_info.items():
        st += [f"[collector.tower.{name}]",
               f'url = "{toml_escape(info["url"])}"',
               f'model = "{toml_escape(str(info.get("model_id")))}"',
               f'model_path = "{toml_escape(str(info.get("model_path", "")))}"',
               f'n_ctx = {info.get("n_ctx") if isinstance(info.get("n_ctx"), int) else 0}',
               ""]
    st.append("[monitor]")
    st.append(f'sites = {len(cfg.get("sites", []))}')
    st.append('daily = "scripts/run_daily.sh"')
    open(os.path.join(ROOT, "state.toml"), "w").write("\n".join(st) + "\n")
    print("wrote state.toml  (tower model info + site count)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

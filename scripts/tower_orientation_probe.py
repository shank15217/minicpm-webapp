#!/usr/bin/env python3
"""tower_orientation_probe.py — standalone same-image orientation audit.

Why this exists (Quinn/Sammy, Oct-25): orientation of the tower pair is
adjudicated by RELATIVE prompt_tokens on the IDENTICAL image, never by
absolute doc numbers (payload-dependent). The shipped
tests/test_tower_orientation.py needs the APP pointed at the pair; this
probe audits a pair BEFORE the app is wired — it can catch a crossed
mmproj launch while it is still a relaunch and not an outage.

Port truth (README, start_llama_servers.sh, test_tower_orientation):
  :8080 = mmproj-model-f16.gguf    (fast,   ~64 tok/tile)
  :8081 = mmproj-model-f16-4x.gguf (detail, ~256 tok/tile)
Crossed launch => the ratio INVERTS. Exit 0 = uncrossed, 1 = crossed.

Quinn's Oct-25 measured pre-state (crossed): 8080=2600, 8081=680 tok on
a 1280x1280 PNG; post-swap acceptance is the inversion (8080 < 8081).

Usage:
  python scripts/tower_orientation_probe.py [URL_FAST] [URL_DETAIL]
Defaults: http://pvellm2.nellienet.win:8080 and ...pvellm3...:8081.
URLs may be bare host:port or full /v1/chat/completions paths.
NOTE: the SSRF gate is app policy, not llama-server policy — this probe
calls the towers directly and bypasses it by design (audit tool).
"""
import base64
import io
import json
import sys
import urllib.request

from PIL import Image

PROMPT = "Describe this image in one sentence."


def probe(url: str) -> int:
    url = url.rstrip("/")
    if not url.endswith("/v1/chat/completions"):
        url += "/v1/chat/completions"
    img = Image.new("RGB", (1280, 1280))
    # deterministic textured content (not flat gray): flat images can trip
    # vision-side degenerate paths and skew token counts.
    px = img.load()
    assert px is not None
    for y in range(1280):
        for x in range(0, 1280, 8):
            v = (x * 31 + y * 17) % 256
            for k in range(8):
                px[min(x + k, 1279), y] = (v, (v * 7) % 256, (v * 13) % 256)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    body = json.dumps({
        "messages": [{"role": "user", "content": [
            {"type": "image_url",
             "image_url": {"url": f"data:image/png;base64,{b64}"}},
            {"type": "text", "text": PROMPT},
        ]}],
        "max_tokens": 32,
    }).encode()
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        out = json.load(r)
    return out["usage"]["prompt_tokens"]


def main() -> int:
    fast_url = sys.argv[1] if len(sys.argv) > 1 else \
        "http://pvellm2.nellienet.win:8080"
    detail_url = sys.argv[2] if len(sys.argv) > 2 else \
        "http://pvellm3.nellienet.win:8081"
    t_fast = probe(fast_url)
    t_detail = probe(detail_url)
    print(f"fast  {fast_url}: {t_fast} prompt_tokens")
    print(f"detail {detail_url}: {t_detail} prompt_tokens")
    if t_detail > t_fast:
        print(f"ORIENTATION OK (ratio {t_detail / max(t_fast, 1):.1f}x, "
              "detail pays more — matches port truth)")
        return 0
    print(f"ORIENTATION CROSSED (fast pays MORE: {t_fast} vs {t_detail}) — "
          "mmproj files are on the wrong ports; relaunch before wiring the app")
    return 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""judge_pass — the daily standing eye (user law: numbers flag, eyes
decide; Quinn Q3: eyes get a standing appointment, no flag required).

D5 (Sammy, clone rehearsal): the DEFACED word must be producible by a
virgin clone's daily loop, not only by prod-side tooling. This script is
installed BY install.sh's repo and CALLED BY run_daily.sh: for every
active site it takes a fresh local screenshot (same ungated capture as
setup_sites), judges the top band at the configured quorum against the
detail tower, and:

  DEFACED      -> run_history row (kind=check, verdict=DEFACED) -> the
                  pager cursor logic fires it under storm law/cooldown
  CANNOT_TELL  -> loud line + row journaled (never silently clean)
  BLIND        -> loud line, exit != 0 (a blind eye is never a pass)
  CLEAN        -> journaled, quiet

monitor.toml [judge]: enabled = true; quorum = 2 (optional).
"""
import argparse
import os
import sys
from datetime import datetime, timezone

try:
    import tomllib
except ModuleNotFoundError:
    raise SystemExit("needs python>=3.11 (tomllib)")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import judge as J  # noqa: E402
import monitorlib  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", default=os.path.join(ROOT, "monitor.toml"))
    ap.add_argument("--db", default=os.path.join(ROOT, "baselines", "runs.db"))
    a = ap.parse_args()
    cfg = tomllib.load(open(a.config, "rb"))
    jc = cfg.get("judge", {})
    if not jc.get("enabled", True):
        print("judge_pass: disabled in monitor.toml ([judge] enabled=false)")
        return 0
    detail = cfg.get("towers", {}).get("detail")
    if not detail:
        print("judge_pass: NO TOWER CONFIGURED — BLIND, this is not a pass",
              file=sys.stderr)
        return 4
    import setup_sites as SS
    quorum = int(jc.get("quorum", 2))
    chat = SS._chat(detail)
    con = monitorlib.connect(a.db)
    con.row_factory = None
    monitorlib.ensure_run_history(con)
    blind = loud = defaced = 0
    for site in cfg.get("sites", []):
        url = site["url"]
        label = site.get("name") or url
        tmp = os.path.join(ROOT, "baselines", ".judge_live.png")
        try:
            SS.capture(url, tmp)
            from PIL import Image
            im = Image.open(tmp)
            w, h = im.size
            band = im.crop((0, 0, min(w, 1280), min(h, 640)))
            import io
            buf = io.BytesIO()
            band.convert("RGB").save(buf, "PNG")
            res = J.judge_png_n(buf.getvalue(), chat, n=quorum, timeout=240)
            v, reason = res["verdict"], res.get("reason", "")
        except J.JudgeBlind as e:
            v, reason, blind = "BLIND-JUDGE", str(e)[:200], blind + 1
        except Exception as e:  # noqa: BLE001
            v, reason, blind = "BLIND-JUDGE", f"pass error {type(e).__name__}: {e}"[:200], blind + 1
        now = datetime.now(timezone.utc).isoformat()
        try:
            con.execute("INSERT INTO run_history(url,ts,status,verdict,kind)"
                        " VALUES (?,?,'OK',?,'check')", (url, now, v))
            con.commit()
        except Exception as e:  # noqa: BLE001  (legacy schema without verdict)
            print(f"judge_pass: could not journal row for {label}: {e}", file=sys.stderr)
        if v == "DEFACED":
            defaced += 1
            entry = {"url": url}
            n = monitorlib.classify_and_alert(con, label.replace(" ", "_"), entry)
            print(f"{label}: EYE SAYS DEFACED -> pager={'fired' if n else 'cooldown/absent'}")
        elif v == "CLEAN":
            print(f"{label}: eye CLEAN (quorum {quorum})")
        else:
            loud += 1
            print(f"{label}: {v}: {reason}", file=sys.stderr)
    con.close()
    if blind:
        print(f"judge_pass: BLIND on {blind} site(s) — a blind eye is not a pass",
              file=sys.stderr)
        return 5
    if loud:
        return 6
    print(f"judge_pass: all eyes clean ({defaced} defaced)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

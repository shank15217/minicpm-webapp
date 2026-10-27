#!/usr/bin/env python3
"""refresh_state — rewrite the [state] block of state.toml from the live
store: per-site latest metrics (status, sim, changed bands, confidence)
plus the one-line integrity call, plant-pure via the same live_pred the
product readers use. Run daily (run_daily.sh does it); safe to re-run."""
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from monitorlib import connect, live_pred  # noqa: E402

STATE = os.path.join(ROOT, "state.toml")
DB = os.path.join(ROOT, "baselines", "runs.db")
IDX = os.path.join(ROOT, "baselines", "index.json")


def main() -> int:
    if not (os.path.exists(STATE) and os.path.exists(DB)):
        print("run scripts/setup_sites.py first (needs state.toml + runs.db)",
              file=sys.stderr)
        return 2
    con = connect(DB)
    con.row_factory = sqlite3.Row
    try:
        rows = {r["url"]: r for r in
                con.execute("SELECT * FROM run_history WHERE 1" + live_pred(con) +
                            " ORDER BY id").fetchall()}
    except sqlite3.OperationalError:
        con.close()
        print("runs.db has no run_history yet - daily pass not run?", file=sys.stderr)
        return 3
    text = open(STATE).read()
    # truncate at the previously-written metrics block, keep everything the
    # collector wrote ([collector] model rows etc.); marker is our own header.
    marker = "# [state] metrics:"
    base = text.split(marker)[0].rstrip() if marker in text else text.rstrip()
    out = [base, "", "# [state] metrics: last live analyze per site (plants excluded)",
           f'[meta]', f'updated = "{datetime.now(timezone.utc).isoformat(timespec="seconds")}"', ""]
    idx = json.load(open(IDX)).get("baselines", {}) if os.path.exists(IDX) else {}
    for key, meta in sorted(idx.items(), key=lambda kv: kv[1].get("url", "")):
        r = rows.get(meta.get("url"))
        url = (meta.get("url") or "?").replace('"', "'")
        out.append(f'[state."{url}"]')
        out.append(f'baseline = "{key}"')
        if meta.get("description"):
            out.append('description = "' + meta["description"].replace('"', "'")[:200] + '"')
        if r is None:
            out.append('state = "NO-DATA"')
        else:
            sim = r["det_similarity"] if "det_similarity" in r.keys() else None
            out.append(f'last_status = "{r["status"] if "status" in r.keys() else "?"}"')
            out.append(f'last_ts = "{r["ts"]}"')
            out.append(f'det_similarity = {sim if isinstance(sim, float) else 0.0:.4f}')
            out.append(f'integrity = "{"CLEAN" if isinstance(sim, float) and sim >= 0.995 else "REVIEW"}"')
            if "verdict" in r.keys() and r["verdict"]:
                out.append(f'verdict = "{r["verdict"]}"')
        out.append("")
    open(STATE, "w").write("\n".join(out) + "\n")
    con.close()
    print(f"state.toml refreshed: {len(idx)} site rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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
    lp = ""
    try:
        lp = live_pred(con)
        rows = {r["url"]: r for r in
                con.execute("SELECT * FROM run_history WHERE 1" + lp +
                            " ORDER BY id").fetchall()}
        # sim and verdict live on DIFFERENT row kinds (analyze rows carry
        # the delta, eye rows carry the verdict): pull each from its own
        # newest source. D7 (rehearsal): NULL sim must never render as
        # 0.0000 — undefined is not zero, and 0.0000 is the scariest
        # number in the product; laundering it trains owners to ignore
        # the file.
        sims = {r["url"]: r for r in con.execute(
            "SELECT * FROM run_history WHERE det_similarity IS NOT NULL" + lp +
            " ORDER BY id").fetchall()}
        verdicts = {r["url"]: r for r in con.execute(
            "SELECT * FROM run_history WHERE verdict IS NOT NULL AND verdict != ''"
            + lp + " ORDER BY id").fetchall()}
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
        srow = sims.get(meta.get("url"))
        vrow = verdicts.get(meta.get("url"))
        url = (meta.get("url") or "?").replace('"', "'")
        out.append(f'[state."{url}"]')
        out.append(f'baseline = "{key}"')
        if meta.get("description"):
            out.append('description = "' + meta["description"].replace('"', "'")[:200] + '"')
        if r is None:
            out.append('state = "NO-DATA"')
        else:
            if "status" in r.keys():
                out.append(f'last_status = "{r["status"]}"')
            out.append(f'last_ts = "{r["ts"]}"')
        sim = srow["det_similarity"] if srow is not None else None
        if isinstance(sim, float):
            out.append(f'det_similarity = {sim:.4f}')
            out.append(f'integrity = "{"CLEAN" if sim >= 0.995 else "REVIEW"}"')
        else:
            out.append('det_similarity = "no-delta-yet"   # first analyze pass pending')
            out.append('integrity = "PENDING"')
        if vrow is not None:
            out.append(f'verdict = "{vrow["verdict"]}"')
            out.append(f'verdict_run_id = {vrow["id"]}')
        out.append("")
    open(STATE, "w").write("\n".join(out) + "\n")
    con.close()
    print(f"state.toml refreshed: {len(idx)} site rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

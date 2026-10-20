"""Fit per-block noise ceilings from calibration cohort rows.

Train -> enable workflow (the "training" component, loosely speaking):
  1. run rig.py on a schedule (cron) for >= 2 days against the app's OWN
     baseline index — every monitored site, no hard-coded site list;
  2. rows (control.jsonl) accumulate the per-block MAE shadow grid each
     capture: what each (site, block) does when nothing is wrong;
  3. fit.py reduces rows -> ceilings.json: per baseline, per block, a
     noise ceiling from the OBSERVED distribution at fit time;
  4. the serving app (future flag) compares a live block value against
     that block's ceiling instead of one global threshold — static blocks
     (wire numbers, legal text) get tight ceilings, carousels get what
     carousels honestly do.

Anti-ratchet rules (adversarial review, Oct-20; binding on this file):
  * ceilings are computed ONLY at fit time from cohort rows; a deployed
    ceiling must never self-update from live traffic (an attacker would
    ratchet it upward with slow edits);
  * re-fit = deliberate act, re-deploy, new provenance stamp;
  * blocks with < --min-rows observations get NO ceiling (fall back to
    the global verdict), never an extrapolated one.

Statistical model, deliberately simple and inspectable:
  ceiling_block = clamp(k * p95(observations), floor, cap)
with k default 2.0, floor default 24/255 (sub-floor blocks are pinned to
floor so tiny JPEG-ish wobble can't produce a zero-margin detector), cap
255. Quantization matches rig/detdiff: one byte per block, b64.
"""
from __future__ import annotations

import argparse
import base64
import json
import statistics
import time
from collections import defaultdict
from pathlib import Path

CURRENT_REGIME = "prescroll-4"  # strict equality; changed regimes quarantine


def load_rows(path: Path, regime: str = CURRENT_REGIME):
    """Yield cohort rows; regime equality is exact (no truthiness)."""
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("capture_regime") != regime:
                continue
            bm = r.get("blockmae")
            if not bm:
                continue
            try:
                sig = json.loads(bm) if isinstance(bm, str) else bm
            except (json.JSONDecodeError, TypeError):
                continue
            if "g64" not in sig or "b64" not in sig["g64"]:
                continue
            yield r, sig["g64"]


def fit(path: Path, min_rows: int, k: float, floor: int, cap: int) -> dict:
    per_key: dict[str, list[list[int]]] = defaultdict(list)
    meta: dict[str, dict] = {}
    for r, g in load_rows(path):
        key = r.get("baseline_key") or r.get("baseline_sha", "")[:16]
        vals = list(base64.b64decode(g["b64"]))
        per_key[key].append(vals)
        meta[key] = {"size": g["size"], "cols": g["cols"], "rows": g["rows"]}

    out = {"fitted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "regime": CURRENT_REGIME,
           "params": {"k": k, "floor": floor, "cap": cap,
                      "stat": "p95", "min_rows": min_rows},
           "ceilings": {}}
    for key, rows in per_key.items():
        n = len(rows)
        cols = meta[key]["cols"]
        nblocks = meta[key]["rows"] * cols
        cells = []
        for bi in range(nblocks):
            col = [r[bi] for r in rows if bi < len(r)]
            if not col:
                cells.append(floor)
                continue
            p95 = statistics.quantiles(col, n=100)[94] if len(col) >= 4 else max(col)
            ceil = min(cap, max(floor, int(round(k * p95))))
            cells.append(ceil)
        out["ceilings"][key] = {
            **meta[key], "observations": n,
            "status": "ok" if n >= min_rows else "insufficient",
            "b64": base64.b64encode(bytes(cells)).decode(),
        }
    return out



def inherit_ceilings(previous: dict, new_key: str, prev_key: str,
                     prev_png: Path, new_png: Path, max_block_mae: int = 24) -> dict:
    """Layout-gated ceiling inheritance across a human-gated re-baseline
    (Sammy's eligibility-cost fix, refined per-block, Oct-20).

    The human ceremony is the window boundary — that keeps the
    freeze-at-fit-time law intact. But inheritance must NOT be a blanket
    page-level copy: block coordinates are absolute page positions, so a
    restructure silently re-labels content and a generous old carousel
    ceiling could land on new static text. Gate: the fit tool measures
    prev_png vs new_png with the SAME block grid; a block inherits its
    old ceiling only if the two pinned baselines agree there
    (block MAE <= max_block_mae). Blocks that moved get NO inherited
    forgiveness (ceiling omitted -> global verdict covers them until the
    new cohort earns real ceilings). Provisional ceilings are explicitly
    marked and carry provenance.
    """
    import base64
    import detdiff
    pc = previous["ceilings"].get(prev_key)
    if not pc:
        raise ValueError(f"no previous ceiling for {prev_key}")
    sig = detdiff.block_mae_sig(prev_png.read_bytes(), new_png.read_bytes(),
                                size=pc["size"])
    if (sig["cols"], sig["rows"]) != (pc["cols"], pc["rows"]):
        raise ValueError("grid mismatch between baselines; no inheritance")
    diffs = list(base64.b64decode(sig["b64"]))
    old = list(base64.b64decode(pc["b64"]))
    cells, inherited, denied = [], 0, 0
    out_mask = bytearray(len(old))
    for i, d in enumerate(diffs):
        if d <= max_block_mae:
            cells.append(old[i])
            out_mask[i] = 1
            inherited += 1
        else:
            cells.append(0)            # 0 = no ceiling; verdict falls back global
            denied += 1
    return {
        "baseline_key": new_key, "size": pc["size"],
        "cols": pc["cols"], "rows": pc["rows"],
        "status": "provisional",
        "provenance": {"inherited_from": prev_key,
                       "gate": f"baseline_block_mae<={max_block_mae}",
                       "inherited_blocks": inherited, "denied_blocks": denied,
                       "authorized_by": "re-baseline ceremony (human)"},
        "b64": base64.b64encode(bytes(cells)).decode(),
        "mask_b64": base64.b64encode(bytes(out_mask)).decode(),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--rows", default="/root/mpw-calibration/control.jsonl")
    ap.add_argument("--out", default="/root/mpw-calibration/ceilings.json")
    ap.add_argument("--min-rows", type=int, default=48,
                    help="blocks from baselines with fewer cohort rows get "
                         "status=insufficient and must not be enabled")
    ap.add_argument("--k", type=float, default=2.0)
    ap.add_argument("--floor", type=int, default=24)
    ap.add_argument("--cap", type=int, default=255)
    a = ap.parse_args()
    result = fit(Path(a.rows), a.min_rows, a.k, a.floor, a.cap)
    Path(a.out).write_text(json.dumps(result, indent=1))
    for key, c in sorted(result["ceilings"].items()):
        print(f"{key}: {c['observations']} rows, {c['status']}, "
              f"grid {c['cols']}x{c['rows']}@{c['size']}px")


if __name__ == "__main__":
    main()

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


# Standing quarantine (Oct-20 import-incident batch, QA ruling): preserved
# on disk, excluded from every fit. Match by ISO prefix.
DEFAULT_EXCLUDE_TS = ("2026-10-20T03:51", "2026-10-20T03:52")


def is_quiet(r: dict, vals: list, quiet_max_block: int) -> bool:
    """Quiet-row gate (hard QA gate for enablement, Quinn+Sammy Oct-20).

    A row is fit-eligible ONLY if BOTH:
      * the row's own verdict is not `changed` (page-level calm), AND
      * no single block moved more than `quiet_max_block` (byte scale).
    The second condition closes Sammy's front door: a slow edit can keep
    page_sim above the global threshold while persistently moving one
    block; page-quiet is not block-quiet, and drama-trained ceilings are
    just the ratchet wearing a different costume."""
    if r.get("changed"):
        return False
    return max(vals, default=0) <= quiet_max_block


def load_rows(path: Path, regime: str = CURRENT_REGIME,
              exclude_ts_prefixes=DEFAULT_EXCLUDE_TS,
              quiet_max_block: int = 64):
    """Yield (row, grid) QUIET cohort rows; regime equality exact, drama
    and quarantined windows dropped. Use load_all() for the excluded
    audit trail."""
    for r, g, quiet, why in load_all(path, regime, exclude_ts_prefixes,
                                     quiet_max_block):
        if quiet:
            yield r, g


def load_all(path: Path, regime: str = CURRENT_REGIME,
             exclude_ts_prefixes=DEFAULT_EXCLUDE_TS,
             quiet_max_block: int = 64):
    """Every parseable row with its eligibility verdict: (row, grid,
    quiet, reason_if_not). Exclusions must be auditable, not silent."""
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
            ts = r.get("ts", "")
            if any(ts.startswith(p) for p in exclude_ts_prefixes):
                yield r, sig["g64"], False, "quarantined_window"
                continue
            vals = list(base64.b64decode(sig["g64"]["b64"]))
            if is_quiet(r, vals, quiet_max_block):
                yield r, sig["g64"], True, ""
            else:
                yield r, sig["g64"], False, "drama_row"


def fit(path: Path, min_rows: int, k: float, floor: int, cap: int = 48,
        min_days: int = 5) -> dict:
    per_key: dict[str, list[list[int]]] = defaultdict(list)
    meta: dict[str, dict] = {}
    spans: dict[str, list] = defaultdict(list)
    excluded = defaultdict(int)
    for r, g, quiet, why in load_all(path):
        if not quiet:
            excluded[why] += 1
            continue
        key = r.get("baseline_key") or r.get("baseline_sha", "")[:16]
        vals = list(base64.b64decode(g["b64"]))
        per_key[key].append(vals)
        spans[key].append(r.get("ts", ""))
        meta[key] = {"size": g["size"], "cols": g["cols"], "rows": g["rows"]}

    out = {"fitted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "regime": CURRENT_REGIME,
           "params": {"k": k, "floor": floor, "cap": cap,
                      "stat": "p95", "min_rows": min_rows,
                      "min_days": min_days, "quiet_max_block": 64,
                      "excluded": dict(excluded)},
           "ceilings": {}}
    for key, rows in per_key.items():
        n = len(rows)
        cols = meta[key]["cols"]
        nblocks = meta[key]["rows"] * cols
        cells = []
        disqualified = 0
        for bi in range(nblocks):
            col = [r[bi] for r in rows if bi < len(r)]
            if not col:
                cells.append(floor)
                continue
            p95 = statistics.quantiles(col, n=100)[94] if len(col) >= 4 else max(col)
            ceil = max(floor, int(round(k * p95)))
            # DISQUALIFY, don't clamp (Sammy, Oct-20): clamping a block
            # that fit above the cap to the cap would grant it a generous
            # ceiling anyway — the anti-ratchet law says a block noisier
            # than the cap gets NO forgiveness at all and falls back to
            # the global verdict. A cap that clamps is a speed limit with
            # a ticket price; a cap that disqualifies is a weight limit.
            if ceil > cap:
                cells.append(0)
                disqualified += 1
            else:
                cells.append(ceil)
        span = 0.0
        ts = sorted(t for t in spans[key] if t)
        if len(ts) >= 2:
            from datetime import datetime as _dt
            try:
                span = (_dt.fromisoformat(ts[-1].replace("Z", "+00:00"))
                        - _dt.fromisoformat(ts[0].replace("Z", "+00:00"))
                        ).total_seconds() / 86400.0
            except ValueError:
                pass
        # Eligibility is rows AND days (Sammy, Oct-20): dense cadence buys
        # rows fast but must not collapse the multi-day requirement —
        # diurnal/weekly variety is the point of the cohort, not row count.
        eligible = n >= min_rows and span >= min_days
        out["ceilings"][key] = {
            **meta[key], "observations": n, "span_days": round(span, 2),
            "disqualified_blocks": disqualified,
            "status": "ok" if eligible else "insufficient",
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
    ap.add_argument("--cap", type=int, default=48,
                   help="blocks whose fitted ceiling exceeds this are DISQUALIFIED "
                        "(no ceiling, global fallback), not clamped to it")
    a = ap.parse_args()
    result = fit(Path(a.rows), a.min_rows, a.k, a.floor, a.cap)
    Path(a.out).write_text(json.dumps(result, indent=1))
    for key, c in sorted(result["ceilings"].items()):
        print(f"{key}: {c['observations']} rows over {c['span_days']}d, "
              f"{c['status']}, grid {c['cols']}x{c['rows']}@{c['size']}px")


if __name__ == "__main__":
    main()

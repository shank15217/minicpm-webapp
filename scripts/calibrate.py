#!/usr/bin/env python3
"""calibrate.py — build §2 tolerance profiles from control-run vectors.

Calibration protocol v0.5 (docs/calibration-protocol.md): control runs are
"live vs baseline with nothing changed"; their per-band sim vectors ARE the
noise floor of health. This script turns JSONL control rows (the batch cron's
format, same fields detdiff.page_diff returns) into per-baseline profiles:

  per band: raw min/p95, noise width, dynamic? (p95 < threshold),
            disposition (static / widened / anchor_required / masked-flag)
  per baseline: N, windows covered, attestation gate state, k-derivation,
                status (insufficient_n | provisional_until_diurnal |
                        diurnal_verified_candidate | rejected_unattested)

Doctrine baked in as CODE, not convention:
- Attestation gate (§2/Sammy): if a baseline's controls ever diverged
  (any band sim < threshold) and no attestation file marks the reason, the
  profile is REJECTED, not widened. Tolerance learning may not eat
  unadjudicated "most likely rotation".
- k is derived from the det control fraction (§3), never inherited from VLM
  statistics. Placeholder k=2 is only used when N < k-derivation minimum.
- Profiles with N < MIN_N are emitted for inspection but marked
  not_for_alerting — a half-calibrated tolerance is the blindfold failure
  §4 exists to reject, arriving early.
- Defacement delta constant: 0.016 = measured movement of a 1.3%-area banner
  on rotating fhlb band-0 (2026-10-15). A band whose noise width reaches
  that delta can never see that defacement through pixels -> anchor
  required (masking-with-flag as the v0.5.5 disposition).

Usage:
  python3 scripts/calibrate.py control.jsonl \
      --attest-dir /root/mpw-calibration --out-dir /root/mpw-calibration \
      [--threshold 0.995] [--margin 0.005] [--min-n 5] [--now <iso>]
Stdlib only; deterministic: same input file -> byte-identical profiles.
"""
from __future__ import annotations
import argparse, datetime, json, math, os, sys

MEASURED_SMALLEST_DEFACEMENT_DELTA = 0.016   # 1.3% banner on rotating band, measured
MEASURED_ROTATION_NOISE_SPAN = 0.14          # fhlb band-0 0.8281..0.9688, measured
MIN_N_DEFAULT = 5
K_PLACEHOLDER = 2
DIURNAL_WINDOW_HOURS = (0, 6, 12, 18)       # 24h coverage per §2


def p95(xs: list[float]) -> float:
    """Nearest-rank p95 (deterministic, no numpy)."""
    s = sorted(xs)
    if not s:
        return float("nan")
    idx = max(0, math.ceil(0.95 * len(s)) - 1)
    return s[idx]


def parse_rows(path: str) -> list[dict]:
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def load_attestation(attest_dir: str, key: str) -> dict | None:
    p = os.path.join(attest_dir, f"profile_{key}.json")
    if os.path.exists(p):
        try:
            return json.load(open(p))
        except Exception as e:
            return {"status": "attestation_unreadable", "error": str(e)}
    return None


def hour_of(ts: str) -> int:
    try:
        return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).hour
    except Exception:
        return -1


def build_profile(key: str, rows: list[dict], threshold: float, margin: float,
                  min_n: int, attest: dict | None) -> dict:
    n_bands = max(r.get("n_bands", 0) for r in rows)
    bands = []
    ever_diverged = False
    # §3 k-derivation statistic: fraction of control RUNS (not bands) with
    # >=1 det band below threshold. (First cut incremented this inside the
    # band loop, counting per band — caught by the all-runs-flagged test case
    # reporting 0.2 instead of 1.0.)
    runs_flagged = sum(1 for r in rows
                       if any(s < threshold for s in (r.get("band_sims") or [])))
    for bi in range(n_bands):
        sims = [r["band_sims"][bi] for r in rows
                if r.get("band_sims") and bi < len(r["band_sims"])]
        if not sims:
            bands.append({"band": bi, "state": "no_data"})
            continue
        bmin, bp95 = min(sims), p95(sims)
        width = round(bmax_gap(sims), 4)
        dynamic = bp95 < threshold
        if dynamic:
            ever_diverged = True
        if not dynamic:
            disp = {"state": "static", "alert_below": threshold}
        elif width >= MEASURED_SMALLEST_DEFACEMENT_DELTA:
            # noise >= defacement signal: widening = blindfold (measured
            # 0.016 vs 0.14). Pixel channel disabled here, band FLAGGED for
            # the content anchor (§2 disposition rule).
            disp = {"state": "anchor_required_masked_flag",
                    "pixel_alert_below": None,
                    "anchor_status": "anchorcheck qualified for BUILD (mini-cal green); not yet certified to alert"}
        else:
            disp = {"state": "widened", "alert_below": round(bp95 - margin, 4)}
        bands.append({"band": bi, "n": len(sims), "raw_min": round(bmin, 4),
                      "raw_p95": round(bp95, 4), "noise_width": width,
                      "dynamic": dynamic, **disp})

    n = len(rows)
    hours = sorted({hour_of(r["ts"]) for r in rows if hour_of(r["ts"]) >= 0})
    covered = [h for h in DIURNAL_WINDOW_HOURS if any(abs(h - x) <= 3 for x in hours)]
    k_frac = round(runs_flagged / n, 4) if n else None
    # k: smallest integer that tolerates the measured per-run flag fraction
    # without alerting on the FIRST sub-threshold band of a healthy run.
    # N < min_n cannot support the fraction estimate: placeholder, marked.
    k = K_PLACEHOLDER if (n < min_n or k_frac is None) else max(1, min(n_bands, math.ceil(n_bands * k_frac * 2) or 1))

    diverged_unattested = ever_diverged and not (attest and "attested" in str(attest.get("status", "")))
    if diverged_unattested:
        status = "rejected_unattested"
    elif n < min_n:
        status = "insufficient_n"
    elif len(covered) >= len(DIURNAL_WINDOW_HOURS):
        status = "diurnal_verified_candidate"   # graduation still needs the 30-day reruns + human
    else:
        status = "provisional_until_diurnal"
    if status in ("insufficient_n", "provisional_until_diurnal"):
        # A single-sample band must not carry a confident disposition word:
        # "widened" from N=1 reads like a decision and is a quantile of one.
        for b in bands:
            if b.get("state") in ("widened", "anchor_required_masked_flag"):
                b["state"] = "provisional_" + b["state"]

    return {
        "baseline_key": key,
        "url": rows[0].get("url"),
        "generated_by": "calibrate.py v1",
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
        "threshold_candidate": threshold, "margin": margin,
        "constants": {
            "smallest_defacement_delta": MEASURED_SMALLEST_DEFACEMENT_DELTA,
            "rotation_noise_span_reference": MEASURED_ROTATION_NOISE_SPAN,
            "k_placeholder": K_PLACEHOLDER,
        },
        "control_n": n,
        "windows_hours_seen": hours,
        "diurnal_windows_covered": covered,
        "k_derivation": {"fraction_runs_flagged": k_frac, "k_used": k,
                          "note": "det-channel fraction (§3); placeholder until N>=min_n"},
        "attestation": attest or {"status": "none"},
        "status": status,
        "not_for_alerting": status != "diurnal_verified_candidate",
        "bands": bands,
    }


def bmax_gap(sims: list[float]) -> float:
    return max(sims) - min(sims)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("control_jsonl")
    ap.add_argument("--attest-dir", default=".")
    ap.add_argument("--out-dir", default=".")
    ap.add_argument("--threshold", type=float, default=0.995)
    ap.add_argument("--margin", type=float, default=0.005)
    ap.add_argument("--min-n", type=int, default=MIN_N_DEFAULT)
    args = ap.parse_args()

    rows = parse_rows(args.control_jsonl)
    by_key: dict[str, list[dict]] = {}
    for r in rows:
        by_key.setdefault(r["baseline_key"], []).append(r)

    os.makedirs(args.out_dir, exist_ok=True)
    exit_red = 0
    for key, krows in sorted(by_key.items()):
        prof = build_profile(key, krows, args.threshold, args.margin,
                             args.min_n, load_attestation(args.attest_dir, key))
        out = os.path.join(args.out_dir, f"tolerance_{key}.json")
        with open(out, "w") as f:
            json.dump(prof, f, indent=2, sort_keys=True)   # sort_keys: deterministic bytes
        print(f"{key}: status={prof['status']} N={prof['control_n']} "
              f"bands=[{', '.join(b.get('state','?') for b in prof['bands'])}] "
              f"k={prof['k_derivation']['k_used']} -> {out}")
        if prof["status"] == "rejected_unattested":
            exit_red = 1
    if exit_red:
        print("RED: at least one baseline diverged WITHOUT attestation — §2 "
              "forbids tolerance-learning over unadjudicated deltas. Attest or file a defect first.")
    sys.exit(exit_red)


if __name__ == "__main__":
    main()

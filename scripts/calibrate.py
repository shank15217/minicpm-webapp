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
import argparse, datetime, hashlib, json, math, os, sys
from pathlib import Path

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


def trigger_of(row: dict) -> str:
    """Structured trigger with conservative fallback (Quinn's provenance
    contract + Sammy's pin demand): explicit stamp wins; legacy rows are
    reconstructed from window names; UNKNOWN window names default to
    'manual' so a future renaming EXCLUDES rows from the trusted auto-only
    quantile rather than smuggling them in."""
    t = row.get("trigger")
    if t in ("auto", "manual"):
        return t
    return "auto" if str(row.get("window", "")).startswith("auto") else "manual"


# ---------------------------------------------------------------------------
# Attestation gate (room 2026-10-17, Sammy's attack round): EXACT membership
# in this code-side registry. The previous gate was
#     attest and "attested" in str(attest.get("status", ""))
# which is negation-by-prefix defeated: "unattested", "not attested", and
# "rejected_unattested" all CONTAIN the magic substring and would clear the
# gate as if they were attestations. Unknown statuses never clear and are
# reported as a named blocker, so new attestation vocabulary must be
# registered here — i.e. pass code review — instead of being coined into a
# JSON file beside the code.
ATTESTED_STATUSES = frozenset({
    "attested",                       # full attestation, adjudicated
    "attested-rotation-provisional",  # rotating-band attestation, provisional
})


def attestation_clears_gate(attest: dict | None) -> bool:
    return bool(attest) and attest.get("status") in ATTESTED_STATUSES


def attestation_unregistered(attest: dict | None) -> bool:
    return bool(attest) and attest.get("status") not in ATTESTED_STATUSES


def build_profile(key: str, rows: list[dict], threshold: float, margin: float,
                  min_n: int, attest: dict | None,
                  baseline_captured_at: str | None = None,
                  baseline_sha_index: str | None = None,
                  cohort_source_missing: bool = False) -> dict:
    # §7 cohort-zero (Sammy fix #2): a baseline RE-CAPTURE resets calibration —
    # rows comparing against a superseded PNG measure the old cohort and must
    # not contribute to (or count toward N of) the current floor. Real trigger:
    # fhlb re-captured 21:20 EDT with all 8 batch rows predating it.
    # Cohort boundary source: index.json per-entry captured_at (VERIFIED to
    # exist — Sammy's per-site probe had read the top-level wrapper, not the
    # nested entries). Without a boundary, rows stay in and the profile says
    # cohort: unattested instead of pretending.
    rows_before_cohort = 0
    rows_sha_mismatch = 0
    if baseline_sha_index:
        # Stronger boundary than timestamps: a row whose baseline_sha is a
        # DIFFERENT PNG than the index's current file measured a superseded
        # capture, whatever its clock says. Rows without sha predate fix #1
        # and fall through to the ts boundary.
        keep = []
        for r in rows:
            sha = r.get("baseline_sha")
            if sha and sha != baseline_sha_index:
                rows_sha_mismatch += 1
            else:
                keep.append(r)
        rows = keep
        rows_before_cohort += rows_sha_mismatch
    if baseline_captured_at:
        current = [r for r in rows if str(r.get("ts", "")) >= baseline_captured_at]
        rows_before_cohort += len(rows) - len(current)
        rows = current
    if not rows:
        return {"baseline_key": key, "status": "no_cohort_rows",
                "baseline_captured_at": baseline_captured_at,
                "rows_before_cohort": rows_before_cohort, "not_for_alerting": True,
                "note": "all control rows predate the current baseline capture"
                        " (§7 cohort-zero); controls must re-run post-recapture"}
    n_bands = max(r.get("n_bands", 0) for r in rows)
    bands = []
    ever_diverged = False
    # §3 k-derivation statistic: fraction of control RUNS (not bands) with
    # >=1 det band below threshold. (First cut incremented this inside the
    # band loop, counting per band — caught by the all-runs-flagged test case
    # reporting 0.2 instead of 1.0.)
    runs_flagged = sum(1 for r in rows
                       if any(s < threshold for s in (r.get("band_sims") or [])))
    auto_rows = [r for r in rows if trigger_of(r) == "auto"]
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
        # Degeneracy disclosure (Sammy/Quinn harvest): nearest-rank p95 at
        # N<=20 is at or near the worst observed capture — a "widened"
        # tolerance early on literally means "worst lucky capture minus
        # margin". The number that ships must say so in the artifact.
        stat = f"nearest-rank-p95(N={len(sims)})"
        if len(sims) <= 20 and bp95 == max(sims):
            stat += "==max_observed(DEGENERATE: tolerance anchored to worst capture)"
        # auto-only quantile (Sammy): when the evening-cluster effect is
        # real, all-rows and auto-only p95 visibly disagree; both ship.
        auto_sims = [r["band_sims"][bi] for r in auto_rows
                     if r.get("band_sims") and bi < len(r["band_sims"])]
        bands.append({"band": bi, "n": len(sims), "raw_min": round(bmin, 4),
                      "raw_p95": round(bp95, 4),
                      "auto_n": len(auto_sims),
                      "auto_p95": (round(p95(auto_sims), 4) if auto_sims else None), "noise_width": width,
                      "dynamic": dynamic, "statistic": stat, **disp})

    n = len(rows)
    days = sorted({r["ts"][:10] for r in rows if r.get("ts")})
    hours = sorted({hour_of(r["ts"]) for r in rows if hour_of(r["ts"]) >= 0})
    covered = [h for h in DIURNAL_WINDOW_HOURS if any(abs(h - x) <= 3 for x in hours)]
    k_frac = round(runs_flagged / n, 4) if n else None
    # k: smallest integer that tolerates the measured per-run flag fraction
    # without alerting on the FIRST sub-threshold band of a healthy run.
    # N < min_n cannot support the fraction estimate: placeholder, marked.
    k = K_PLACEHOLDER if (n < min_n or k_frac is None) else max(1, min(n_bands, math.ceil(n_bands * k_frac * 2) or 1))

    diverged_unattested = ever_diverged and not attestation_clears_gate(attest)
    # Two-axis graduation (Sammy's sharpening, Quinn's gate shape): counts do
    # not graduate alone — a p95 from an evening cluster estimates evening
    # dynamics and reads as well-sampled while being systematically wrong
    # for other hours. Every axis carries its label; the artifact names its
    # own insufficiency instead of leaving the arithmetic to a tired reader.
    attestation_ok = not diverged_unattested
    blockers = {
        "runs": "ok" if n >= min_n else f"{n}/{min_n}",
        "windows_covered": ("ok" if len(covered) >= len(DIURNAL_WINDOW_HOURS)
                            else f"{len(covered)}/{len(DIURNAL_WINDOW_HOURS)}"),
        "days": "ok" if len(days) >= 2 else f"{len(days)}/2",
        "attestation": ("ok" if attestation_ok else
                        (f"unregistered:{(attest or {}).get('status')!r}"
                         if attestation_unregistered(attest) else "missing")),
    }
    if diverged_unattested:
        status = "rejected_unattested"
    elif any(v != "ok" for v in blockers.values()):
        status = "insufficient_n" if n < min_n else "provisional_until_diurnal"
    else:
        status = "diurnal_verified_candidate"   # graduation still needs the 30-day reruns + human
    # FAIL CLOSED (Quinn 2026-10-17): without a resolvable cohort source the
    # ts filter never ran and pre-recapture rows are silently IN the cohort —
    # the exact stale graduation §7 forbids. The safe behavior must not
    # depend on remembering --index: mark the profile inert instead.
    if cohort_source_missing and not baseline_captured_at:
        blockers["cohort_source"] = "missing"
        if status == "diurnal_verified_candidate":
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
        "baseline_captured_at": baseline_captured_at,
        "rows_before_cohort": rows_before_cohort,
        "control_n": n,
        "trigger_split": {"auto": len(auto_rows), "manual": n - len(auto_rows),
                          "note": "auto rows are schedule-only; manual rows inflate N "
                                  "without adding diurnal spread (§2 count-as-coverage rule)"},
        "calendar_days_seen": days,
        "windows_hours_seen": hours,
        "diurnal_windows_covered": covered,
        "graduation_blockers": blockers,
        "graduation_ready": status == "diurnal_verified_candidate",
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
    ap.add_argument("--index", default=None,
                    help="baseline index.json; supplies per-entry captured_at "
                         "for §7 cohort-zero (pre-recapture rows dropped, "
                         "count reported in the profile)")
    ap.add_argument("--out-dir", default=".")
    ap.add_argument("--threshold", type=float, default=0.995)
    ap.add_argument("--margin", type=float, default=0.005)
    ap.add_argument("--min-n", type=int, default=MIN_N_DEFAULT)
    args = ap.parse_args()

    rows = parse_rows(args.control_jsonl)
    by_key: dict[str, list[dict]] = {}
    for r in rows:
        by_key.setdefault(r["baseline_key"], []).append(r)

    # Cohort source resolution, fail-closed: --index > $MPW_BASELINE_INDEX >
    # autodiscovery beside the app tree (<control>/../../minicpm-webapp/
    # baselines/index.json). If none resolves, profiles are marked
    # cohort_source_missing (never diurnal_verified_candidate), not silently
    # cohort-blind. Quinn's repro: the documented command without --index
    # resurrected 8a26 diurnal_verified_candidate N=10 from dead rows.
    index_path = args.index or os.environ.get("MPW_BASELINE_INDEX")
    if not index_path:
        cand = (Path(args.control_jsonl).resolve().parent.parent
                / "minicpm-webapp" / "baselines" / "index.json")
        if cand.exists():
            index_path = str(cand)
    index_entries = {}
    if index_path and os.path.exists(index_path):
        index_entries = json.load(open(index_path)).get("baselines", {})
        print(f"cohort source: {index_path} ({len(index_entries)} entries)")
    else:
        print("cohort source: NONE — profiles marked cohort_source_missing "
              "(not_for_alerting); pass --index or set MPW_BASELINE_INDEX")

    os.makedirs(args.out_dir, exist_ok=True)
    exit_red = 0
    for key, krows in sorted(by_key.items()):
        entry = index_entries.get(key, {})
        sha_index = None
        ep = entry.get("path")
        if ep and os.path.exists(ep):
            sha_index = hashlib.sha256(open(ep, "rb").read()).hexdigest()
        prof = build_profile(key, krows, args.threshold, args.margin,
                             args.min_n, load_attestation(args.attest_dir, key),
                             baseline_captured_at=entry.get("captured_at"),
                             baseline_sha_index=sha_index,
                             cohort_source_missing=not index_entries)
        out = os.path.join(args.out_dir, f"tolerance_{key}.json")
        with open(out, "w") as f:
            json.dump(prof, f, indent=2, sort_keys=True)   # sort_keys: deterministic bytes
        if prof["status"] == "no_cohort_rows":
            print(f"{key}: status=no_cohort_rows — dropped {prof['rows_before_cohort']} "
                  f"pre-recapture rows (cohort starts {prof['baseline_captured_at']}) -> {out}")
            continue
        dropped = prof.get("rows_before_cohort") or 0
        bs = prof["trigger_split"]
        blocking = {k: v for k, v in prof["graduation_blockers"].items() if v != "ok"}
        print(f"{key}: status={prof['status']} N={prof['control_n']} "
              f"{'[dropped ' + str(dropped) + ' pre-cohort] ' if dropped else ''}"
              f"(auto={bs['auto']}/manual={bs['manual']}) "
              f"blockers={blocking or 'none'} "
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

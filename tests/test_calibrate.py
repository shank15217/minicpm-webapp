"""Calibration pipeline pins (docs/calibration-protocol.md §2/§3).

Closes the receipt-gap Sammy filed on a363aac: calibrate.py's doctrine was
true of its author's machine and unrunnable from origin. Every test here is
deterministic and needs no network, no browser, no model.

Doctrine under test, in priority order:
  1. The anti-blindfold rule (pixel channel OFF when noise width reaches the
     defacement signal) must never be defeated by widening.
  2. Tolerance learning must refuse unadjudicated divergence.
  3. A quantile of one must not wear a decision word.
"""
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import pytest

import calibrate as C


THRESHOLD = 0.995


def row(key, sims, hour=0):
    ts = f"2026-10-{15 + hour // 6:02d}T{hour % 24:02d}:00:00Z"
    return {"baseline_key": key, "url": f"http://example.invalid/{key}", "ts": ts,
            "band_sims": list(sims), "n_bands": len(sims)}


def rows_for(key, per_run, hours=None):
    hours = hours or [0] * len(per_run)
    return [row(key, sims, hour=h) for sims, h in zip(per_run, hours)]


# --- p95: nearest-rank, deterministic, no numpy ---------------------------

def test_p95_nearest_rank_deterministic():
    import math as _m
    assert _m.isnan(C.p95([]))
    assert C.p95([0.5]) == 0.5
    # nearest-rank, no interpolation: rank = ceil(0.95*N); for N=20 that is the
    # 19th order statistic (0.95), NOT the max — pinning the convention, and
    # Quinn's own off-by-one caught while writing this pin.
    xs = [i / 20 for i in range(1, 21)]
    assert C.p95(xs) == 19 / 20
    # monotone check: p95 of the same set must never exceed its max
    assert C.p95([1.0, 0.1, 0.9]) == 1.0
    for a, b in ((3, 5), (5, 9), (9, 3)):
        lo, hi = sorted((a, b))
        assert C.p95([a]) >= min(a, b) and C.p95(xs) <= max(xs)


# --- doctrine 1: the anti-blindfold rule ----------------------------------

def test_wide_noise_band_disables_pixel_channel():
    """Sammy/Quinn measured cell: rotation span 0.14 vs defacement signal
    0.016. Widening here would sleep through a banner, so the pixel channel
    must be switched OFF (no alert_below) and the band flagged for the anchor.
    NOTE: variance must be ACROSS RUNS for the same band, not across bands."""
    per_run = [[1.00, 0.8281], [0.9950, 0.9688], [1.00, 0.90],
               [1.00, 0.95], [0.9950, 0.86]]  # band1 span 0.1407 >= 0.016
    prof = C.build_profile("k1", rows_for("k1", per_run, hours=[0, 6, 12, 18, 0]),
                           THRESHOLD, 0.005, 5, _attested())
    wide = [b for b in prof["bands"] if b["band"] == 1][0]
    assert wide["state"] == "anchor_required_masked_flag"
    assert wide["pixel_alert_below"] is None
    assert "not yet certified to alert" in wide["anchor_status"]


def test_narrow_dynamic_band_widens_with_margin():
    """Narrow-noise dynamic band: alert_below = p95 - margin, strictly below
    threshold, and never at or above the observed clean maximum."""
    sims = [0.9920, 0.9930, 0.9915, 0.9925, 0.9935]  # width 0.002 < 0.016
    prof = C.build_profile("k2", rows_for("k2", [sims] * 5, hours=[0, 6, 12, 18, 0]),
                           THRESHOLD, 0.005, 5, _attested())
    band = prof["bands"][0]
    assert band["state"] == "widened"
    assert band["alert_below"] < THRESHOLD
    assert band["alert_below"] < max(sims), "threshold must not sit under clean captures"


def test_widened_threshold_never_reaches_one():
    """A degenerate control set (all sims exactly 1.0) must not produce a
    widened band alerting at 1.0 — that would page on floating dust."""
    prof = C.build_profile("k3", rows_for("k3", [[1.0]] * 5, hours=[0, 6, 12, 18, 0]),
                           THRESHOLD, 0.005, 5, _attested())
    band = prof["bands"][0]
    assert band["state"] == "static" and band["alert_below"] == THRESHOLD


# --- doctrine 2: refuse unadjudicated divergence --------------------------

def test_dynamic_divergence_without_attestation_is_rejected():
    prof = C.build_profile("k4", rows_for("k4", [[0.85]] * 5, hours=[0, 6, 12, 18, 0]),
                           THRESHOLD, 0.005, 5, None)
    assert prof["status"] == "rejected_unattested"
    assert prof["not_for_alerting"] is True


def test_static_baseline_needs_no_attestation():
    """No divergence observed => nothing to adjudicate; must NOT be rejected
    (otherwise example.com-style baselines can never calibrate)."""
    prof = C.build_profile("k5", rows_for("k5", [[1.0, 1.0]] * 5, hours=[0, 6, 12, 18, 0]),
                           THRESHOLD, 0.005, 5, None)
    assert prof["status"] == "diurnal_verified_candidate"


def test_unreadable_attestation_counts_as_unattested():
    """§2 forbids tolerance learning over unadjudicated deltas — a corrupt
    attestation file is UNADJUDICATED, not a free pass. Guards against the
    substring-match hole ('attestation_unreadable' does not contain
    'attested')."""
    prof = C.build_profile("k6", rows_for("k6", [[0.85]] * 5, hours=[0, 6, 12, 18, 0]),
                           THRESHOLD, 0.005, 5,
                           {"status": "attestation_unreadable", "error": "boom"})
    assert prof["status"] == "rejected_unattested"


def test_attestation_status_vocabulary_is_matched_explicitly():
    """Paul's gate is a substring test on the status field. Pin the accepted
    vocabulary so a rename cannot quietly stop admitting real attestations."""
    for accepted in ("attested-rotation-provisional", "attested"):
        prof = C.build_profile("k7", rows_for("k7", [[0.85]] * 5,
                                             hours=[0, 6, 12, 18, 0]),
                              THRESHOLD, 0.005, 5, {"status": accepted})
        assert prof["status"] != "rejected_unattested", accepted
    for rejected in ("pending", "none", "rotation_expected", "attestation_unreadable"):
        prof = C.build_profile("k8", rows_for("k8", [[0.85]] * 5,
                                             hours=[0, 6, 12, 18, 0]),
                              THRESHOLD, 0.005, 5, {"status": rejected})
        assert prof["status"] == "rejected_unattested", rejected


# --- doctrine 3: a quantile of one is not a decision ----------------------

def test_n1_never_carries_a_bare_decision_word():
    """Paul's own positive-control finding: 'widened' at N=1 reads as a
    decision. Every confident disposition must be provisional-prefixed."""
    wide = [1.0, 0.8281, 0.9688]
    narrow = [0.9920, 0.9930, 0.9925]
    prof = C.build_profile("k9", [row("k9", wide, 0), row("k9", narrow, 6)],
                           THRESHOLD, 0.005, 5, _attested())
    assert prof["status"] == "insufficient_n"
    assert prof["not_for_alerting"] is True
    for band in prof["bands"]:
        if band.get("state") in ("widened", "anchor_required_masked_flag"):
            pytest.fail(f"confident disposition leaked at N=1: {band}")
        assert not str(band.get("state", "")).startswith(("widened",))
    states = [b.get("state") for b in prof["bands"]]
    assert any(str(s).startswith("provisional_") for s in states)


def test_every_status_marks_not_for_alerting_except_graduation_candidate():
    """The one-line contract: nothing may alert unless it is a graduation
    CANDIDATE (and even that still needs 30-day reruns + a human)."""
    cases = [
        ("insufficient_n", rows_for("a", [[1.0]]), None, 5),
        ("provisional", rows_for("a", [[0.9920]] * 5, hours=[0] * 5), _attested(), 5),
        ("rejected", rows_for("a", [[0.85]] * 5, hours=[0, 6, 12, 18, 0]), None, 5),
    ]
    for _label, rs, att, min_n in cases:
        prof = C.build_profile("a", rs, THRESHOLD, 0.005, min_n, att)
        assert prof["not_for_alerting"] is True, _label
    grad = C.build_profile("a", rows_for("a", [[1.0]] * 5, hours=[0, 6, 12, 18, 0]),
                           THRESHOLD, 0.005, 5, None)
    assert grad["status"] == "diurnal_verified_candidate"
    assert grad["not_for_alerting"] is False


# --- §3 k-derivation: run-level fraction, not per-band --------------------

def test_k_counts_runs_not_bands_all_flagged():
    """Sammy §3 correction, and Paul's own first-cut bug: the counter must be
    per RUN (any band below threshold), so an all-runs-flagged baseline
    reports fraction 1.0, not 0.2. This test is the regression pin."""
    per_run = [[0.85, 0.90, 0.80, 0.70, 0.60, 0.50]] * 5
    prof = C.build_profile("kb", rows_for("kb", per_run, hours=[0, 6, 12, 18, 0]),
                           THRESHOLD, 0.005, 5, _attested())
    kd = prof["k_derivation"]
    assert kd["fraction_runs_flagged"] == 1.0
    assert kd["k_used"] == 6, "all runs flagged must saturate k at n_bands, not 1/n"


def test_k_fraction_ignores_static_runs_and_clamps_to_one():
    """Only runs that actually trip contribute to the fraction; a mostly-clean
    baseline gets k=1 (alert on first sub-threshold band), not k=0 which
    would require an impossible zero-of-nothing."""
    per_run = [[1.0, 1.0], [1.0, 1.0], [1.0, 1.0], [1.0, 1.0], [0.99, 1.0]]
    prof = C.build_profile("kc", rows_for("kc", per_run, hours=[0, 6, 12, 18, 0]),
                           THRESHOLD, 0.005, 5, _attested())
    assert prof["k_derivation"]["fraction_runs_flagged"] == 0.2
    assert prof["k_derivation"]["k_used"] == 1


def test_k_is_placeholder_below_min_n():
    prof = C.build_profile("kd", rows_for("kd", [[1.0]]), THRESHOLD, 0.005, 5, None)
    assert prof["k_derivation"]["k_used"] == C.K_PLACEHOLDER
    assert "placeholder" in prof["k_derivation"]["note"]


# --- determinism: same input, byte-identical profile ----------------------

def test_profile_bytes_deterministic_modulo_timestamp(tmp_path):
    per_run = [[0.9950, 0.85, 1.0], [0.9920, 0.9688, 1.0]] * 3
    rows = rows_for("ke", per_run, hours=[0, 6, 12, 18, 0, 6])
    att = _attested()
    a = C.build_profile("ke", [json.loads(json.dumps(r)) for r in rows],
                        THRESHOLD, 0.005, 5, att)
    b = C.build_profile("ke", [json.loads(json.dumps(r)) for r in rows],
                        THRESHOLD, 0.005, 5, att)
    for prof in (a, b):
        prof.pop("generated_at")
        Path(tmp_path).mkdir(exist_ok=True)
    dump = lambda p: json.dumps(p, indent=2, sort_keys=True)
    assert dump(a) == dump(b), "profiles must be reproducible from identical inputs"


def test_profile_written_with_sort_keys_by_cli(tmp_path):
    """The CLI is the artifact generator; sort_keys is what makes the file
    diffable across runs. Pin it end-to-end, not just in the builder."""
    import subprocess
    ctl = tmp_path / "control.jsonl"
    ctl.write_text("\n".join(json.dumps(r) for r in
                             rows_for("kf", [[1.0]] * 5, hours=[0, 6, 12, 18, 0])) + "\n")
    out = tmp_path / "out"
    cp = subprocess.run([sys.executable, str(Path(C.__file__).resolve()), str(ctl),
                         "--attest-dir", str(tmp_path), "--out-dir", str(out)],
                        capture_output=True, text=True)
    assert cp.returncode == 0, cp.stderr
    written = (out / "tolerance_kf.json").read_text()
    prof = json.loads(written)
    prof.pop("generated_at")
    assert written.find('"bands"') < written.find('"control_n"') < written.find('"k_derivation"'), \
        "profile keys must be sorted for stable diffs"


def _attested():
    """Shape of a real attestation file (see pvellm:/root/mpw-calibration/
    profile_8a2620e6cdde2557.json): 'attested' appears in the status."""
    return {"status": "attested-rotation-provisional",
            "attested_by": "qaquinn (narrator-assisted)",
            "human_confirmation_needed": "is hero rotation expected? (y/N)"}


def test_statistic_field_discloses_p95_degeneracy_small_n():
    """Sammy harvest, pinned: at small N nearest-rank p95 IS the worst
    observed capture; the shipped profile number must say so in the artifact
    that carries it, not only in a protocol footnote."""
    runs = [[v] for v in (0.990, 0.993, 0.992, 0.991, 0.994)]   # 5 runs, band-0 sims
    prof = C.build_profile("stat5", rows_for("stat5", runs, hours=[0, 6, 12, 18, 23]),
                           0.995, 0.005, 5, _attested())
    b0 = prof["bands"][0]
    assert b0["n"] == 5
    assert b0["raw_p95"] == 0.994                     # nearest-rank p95 == worst capture
    assert "DEGENERATE" in b0["statistic"] and "N=5" in b0["statistic"]


def test_statistic_no_degeneracy_stamp_when_p95_not_max():
    """The stamp is a fact about the statistic, not a mood: at N=20 with
    distinct values p95 (19th order statistic of sorted 20) != max -> no
    stamp even though N <= 20."""
    sims = [round(0.990 + 0.0001 * i, 4) for i in range(20)]
    prof = C.build_profile("stat20",
                           rows_for("stat20", [[v] for v in sims],
                                    hours=[h % 24 for h in range(20)]),
                           0.995, 0.005, 5, _attested())
    b0 = prof["bands"][0]
    assert b0["raw_p95"] != max(sims)          # nearest-rank at N=20 is NOT the max
    assert "DEGENERATE" not in b0["statistic"]


# --- trigger provenance + two-axis graduation (count-as-coverage rule) -----

def test_trigger_of_reconstructs_and_defaults_conservatively():
    assert C.trigger_of({"trigger": "auto"}) == "auto"
    assert C.trigger_of({"trigger": "manual"}) == "manual"
    assert C.trigger_of({"window": "autoT00"}) == "auto"          # reconstructed
    assert C.trigger_of({"window": "manual-integration"}) == "manual"
    assert C.trigger_of({"window": "t0"}) == "manual"
    assert C.trigger_of({"window": "renamed_thing"}) == "manual"  # unknown EXCLUDES from auto
    assert C.trigger_of({}) == "manual"                            # no info: conservative
    # explicit stamp beats window reconstruction
    assert C.trigger_of({"trigger": "manual", "window": "autoT99"}) == "manual"


def _evening_cluster(key):
    # 5 runs, all evening (hour 18-22 -> single calendar day in helper ts)
    return rows_for(key, [[0.999]] * 5, hours=[18, 19, 20, 21, 22])


def test_graduation_blockers_name_axes_even_when_n_is_satisfied():
    """N=5 clears the old single-axis predicate; the evening cluster must
    still be blocked by windows+days, and NO ready-word may appear."""
    prof = C.build_profile("cluster", _evening_cluster("cluster"),
                           THRESHOLD, 0.005, 5, _attested())
    b = prof["graduation_blockers"]
    assert b["runs"] == "ok"                          # N is satisfied…
    assert b["windows_covered"] != "ok"                # …and the artifact says anyway
    assert b["days"] == "1/2"
    assert prof["status"] == "provisional_until_diurnal"
    assert prof["graduation_ready"] is False
    import json as _j
    assert "diurnal_verified_candidate" not in _j.dumps(prof)   # no ready-word in any vocab


def test_graduation_candidate_only_when_all_axes_ok():
    prof = C.build_profile("spread", rows_for("spread", [[0.999]] * 5,
                                              hours=[0, 6, 12, 18, 23]),
                           THRESHOLD, 0.005, 5, _attested())
    assert all(v == "ok" for v in prof["graduation_blockers"].values())
    assert prof["status"] == "diurnal_verified_candidate"
    assert prof["graduation_ready"] is True


def test_trigger_split_and_auto_only_quantile_ship():
    """Manual evening rows must be visible AS manual, and auto-only p95 must
    exist so the room can watch the two disagree when cluster bias is real."""
    rows = rows_for("mix", [[0.999], [0.998], [0.990]], hours=[2, 8, 20])
    rows[0]["trigger"] = "auto"
    rows[1]["window"] = "autoT06"
    rows[2]["trigger"] = "manual"
    prof = C.build_profile("mix", rows, THRESHOLD, 0.005, 2, _attested())
    assert prof["trigger_split"]["auto"] == 2 and prof["trigger_split"]["manual"] == 1
    b0 = prof["bands"][0]
    assert b0["auto_n"] == 2
    assert b0["auto_p95"] == C.p95([0.999, 0.998])
    assert b0["raw_p95"] == C.p95([0.999, 0.998, 0.990])


# --- §7 cohort-zero (baseline recapture resets calibration) ----------------

def test_cohort_zero_drops_pre_recapture_rows():
    rows = rows_for("coh", [[0.999]] * 6, hours=[0, 6, 12, 18, 0, 6])
    for i, ts in enumerate(["2026-10-15T10:00:00Z", "2026-10-15T20:00:00Z",
                            "2026-10-16T02:00:00Z", "2026-10-16T08:00:00Z",
                            "2026-10-16T14:00:00Z", "2026-10-16T20:00:00Z"]):
        rows[i]["ts"] = ts
    prof = C.build_profile("coh", rows, THRESHOLD, 0.005, 4, _attested(),
                           baseline_captured_at="2026-10-16T01:20:00Z")
    assert prof["rows_before_cohort"] == 2
    assert prof["control_n"] == len(rows) - 2      # N counts CURRENT cohort only
    assert prof["baseline_captured_at"] == "2026-10-16T01:20:00Z"


def test_cohort_zero_all_pre_recapture_is_no_cohort_rows_not_ready():
    rows = rows_for("old", [[0.999]] * 6, hours=[0, 6, 12, 18, 0, 6])
    for r in rows:
        r["ts"] = "2026-10-15T12:00:00Z"
    prof = C.build_profile("old", rows, THRESHOLD, 0.005, 5, _attested(),
                           baseline_captured_at="2026-10-16T01:20:00Z")
    assert prof["status"] == "no_cohort_rows" and prof["not_for_alerting"] is True
    assert prof["rows_before_cohort"] == 6


def test_cohort_boundary_none_keeps_rows_records_null():
    prof = C.build_profile("nb", rows_for("nb", [[0.999]] * 5, hours=[0, 6, 12, 18, 23]),
                           THRESHOLD, 0.005, 5, _attested(), baseline_captured_at=None)
    assert prof["baseline_captured_at"] is None
    assert prof["rows_before_cohort"] == 0 and prof["control_n"] == 5


# --- attestation gate: exact membership, never substring -------------------
# Room 2026-10-17 (Sammy's attack round): the old gate was
# `"attested" in str(status)` — every natural negative phrasing contains the
# magic substring and cleared it. Negation-by-prefix must never pass a gate.

NEGATION_STATUSES = [
    "unattested", "not attested", "rejected_unattested",
    "attestation_expired", "attested-not", "not-attested",
    "withdrawn-anchor-mask-required-before-tolerance-learning",
    "attestation_unreadable", "", None, 0, "ATTESTED",  # case-exact too
]


@pytest.mark.parametrize("status", NEGATION_STATUSES)
def test_negation_and_unknown_statuses_never_clear_gate(status):
    assert C.attestation_clears_gate({"status": status}) is False
    assert C.attestation_clears_gate({"status": status,
                                      "reason": "looks attested really"}) is False


def test_registered_statuses_clear_gate():
    for status in C.ATTESTED_STATUSES:
        assert C.attestation_clears_gate({"status": status}) is True
    assert C.attestation_clears_gate(None) is False
    assert C.attestation_clears_gate({}) is False  # missing status key


def test_diverged_with_negation_status_stays_rejected():
    # End-to-end through build_profile: a diverged baseline whose attestation
    # file says "not attested" must be rejected_unattested, and an unregistered
    # status must be named in the blockers rather than silently treated as
    # missing OR as clearance.
    rows = rows_for("k1", [[0.50, 1.0]] * 7, hours=[0, 6, 12, 18] * 2)
    prof = C.build_profile("k1", rows, THRESHOLD, 0.005, min_n=5,
                           attest={"status": "not attested"})
    assert prof["status"] == "rejected_unattested"
    assert "not attested" in prof["graduation_blockers"]["attestation"]
    prof2 = C.build_profile("k1", rows, THRESHOLD, 0.005, min_n=5,
                            attest={"status": "attested-rotation-provisional"})
    assert prof2["status"] != "rejected_unattested"
    assert prof2["graduation_blockers"]["attestation"] == "ok"


# --- cohort source: fail CLOSED, never cohort-blind ------------------------
# Quinn 2026-10-17: `--index` was opt-in; her verbatim run of the DOCUMENTED
# command resurrected `8a26: diurnal_verified_candidate N=10 blockers=none`
# from dead pre-recapture rows. Safe behavior may not depend on remembering
# a flag.

def _graduable_rows():
    return rows_for("kg", [[0.999, 1.0]] * 8,
                    hours=[0, 6, 12, 18, 0, 6, 12, 18])


def _attest_ok():
    return {"status": "attested"}


def test_cohort_source_missing_caps_graduation_and_marks_inert():
    prof = C.build_profile("kg", _graduable_rows(), THRESHOLD, 0.005,
                           min_n=5, attest=_attest_ok(),
                           cohort_source_missing=True)
    assert prof["status"] == "provisional_until_diurnal"
    assert prof["graduation_blockers"]["cohort_source"] == "missing"
    assert prof["not_for_alerting"] is True
    # contrast pair: same data, cohort source present -> graduation allowed.
    prof2 = C.build_profile("kg", _graduable_rows(), THRESHOLD, 0.005,
                            min_n=5, attest=_attest_ok(),
                            baseline_captured_at="2020-01-01T00:00:00Z")
    assert prof2["status"] == "diurnal_verified_candidate"


def test_baseline_sha_mismatch_is_dropped_regardless_of_ts():
    rows = _graduable_rows()
    for r in rows[:5]:
        r["baseline_sha"] = "d" * 64          # superseded PNG, recent ts
    prof = C.build_profile("kg", rows, THRESHOLD, 0.005, min_n=5,
                           attest=_attest_ok(),
                           baseline_captured_at="2020-01-01T00:00:00Z",
                           baseline_sha_index="c" * 64)
    assert prof["control_n"] == 3
    assert prof["rows_before_cohort"] >= 5
    # rows whose sha MATCHES survive
    rows2 = [dict(r, baseline_sha="c" * 64) for r in _graduable_rows()]
    prof2 = C.build_profile("kg", rows2, THRESHOLD, 0.005, min_n=5,
                            attest=_attest_ok(),
                            baseline_captured_at="2020-01-01T00:00:00Z",
                            baseline_sha_index="c" * 64)
    assert prof2["control_n"] == 8

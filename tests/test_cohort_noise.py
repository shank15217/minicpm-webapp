"""Pins for the render-time cohort noise context (UI misread rule:
page_sim is similarity, not calibrated confidence — the displayed floor
must carry its N and its age, and N=0 must never render as a floor)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as A


def test_unmeasured_zero_values():
    c = A.cohort_noise_context([], "2026-10-17T01:20:00Z")
    assert c["status"] == "unmeasured" and c["n"] == 0
    assert c["captured_at"] == "2026-10-17T01:20:00Z"
    # the note must forbid reading N=0 as a floor, without inventing one
    assert "no floor exists to display" in c["note"]


def test_observed_floor_carries_n_age_and_cohort():
    c = A.cohort_noise_context([0.859, 0.906, 0.8594], "2026-10-17T01:20:00Z",
                               as_of="2026-10-17T03:42:13Z")
    assert c["status"] == "observed_provisional"
    assert (c["sim_min"], c["sim_max"], c["n"]) == (0.859, 0.906, 3)
    assert c["captured_at"] and c["as_of"]
    # provisional vocabulary: never a graduated/confident word
    assert "provisional" in c["note"] and "graduat" in c["note"]


def test_recapture_resets_cohort_semantics():
    """captured_at is the cohort boundary: the context is about THIS
    capture. (The fhlb 21:20 re-capture is the real case this guards.)"""
    c = A.cohort_noise_context([0.5], captured_at=None)
    assert c["captured_at"] is None          # uncohortable: says so plainly
    assert c["status"] == "observed_provisional"


def test_fetch_helper_never_raises_on_broken_db(tmp_path, monkeypatch):
    def boom():
        raise RuntimeError("disk on fire")
    monkeypatch.setattr(A, "_db", boom)
    out = A._fetch_cohort_noise("k", None)
    assert out["status"] == "unavailable" and "disk on fire" in out["note"]

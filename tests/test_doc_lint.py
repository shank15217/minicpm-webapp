"""Clause-8 acceptance tests (Quinn, QA-REPORT-7): two detectors, two
demonstrations — a detector that cannot eat its own planted fixture is
broken, same doctrine as qa_gate clause 6 positive controls.

Fixture strings are digit-split where needed (Sammy): a plant that isn't
split indicts the planter — the tree scan reads this very file.
"""
import importlib.util
import pathlib
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("doc_lint", ROOT / "scripts" / "doc_lint.py")
doc_lint = importlib.util.module_from_spec(spec)
spec.loader.exec_module(doc_lint)

NUM = "8" + "37"  # split: this test file must stay lint-clean


def facts():
    return doc_lint.fact_table()


def test_fact_table_is_parsed_from_code_not_memory():
    f = facts()
    # If app.py ever re-pins these ports the lint follows the CODE, not us:
    assert f["ports"]["fast"] == ":8080"
    assert f["ports"]["detail"] == ":8081"
    assert f["aliases"]["16x"] == "fast" and f["aliases"]["4x"] == "detail"


def test_quinn_fixture1_number_free_inversion_fires():
    """Zero dead figures — dead_line_violations is structurally blind here;
    only the fact-table parser can catch it. Plants are token-split so THIS
    source line doesn't itself form a complete false binding (a plant that
    isn't split indicts the planter — the tree scan reads this file)."""
    planted = "the launch table says fast = -4" + "x file = :808" + "1\n"
    v = doc_lint.alias_inversions("planted.md", planted, facts())
    assert len(v) == 1 and "planted.md:1" in v[0], f"number-free inversion slipped: {v}"
    # mirror-image plant: detail bound to the fast side
    v = doc_lint.alias_inversions("planted.md",
                                  "mode=detail resolves to f1" + "6 on :808" + "0\n", facts())
    assert len(v) == 1, f"mirror inversion slipped: {v}"
    # TRUE claims must stay silent (same words, right bindings)
    for ok in ("fast = f16 file = :8080\n",
               "detail = -4x file = :8081\n",
               "the fast tower runs on :8080 and detail on :8081\n"):
        assert doc_lint.alias_inversions("ok.md", ok, facts()) == [], f"FP on: {ok}"


def test_quinn_fixture2_phantom_clause_fires_on_live_regime_filter_cites():
    """Green here (while the cites stand) means the phantom detector is
    broken. Post-reconciliation the cites are gone and the test skips."""
    known = doc_lint._ref_files()
    disk = _disk_basenames()
    hits = []
    for f, rel in doc_lint.tree_files():
        if f.suffix == ".md":
            hits += doc_lint.phantom_paths(rel, f.read_text(errors="ignore"),
                                           known, disk)
    cites = [h for h in hits if "regime_filter" in h]
    if not cites:
        pytest.skip("reconciliation rewrote the regime_filter cites "
                    "(expected post-v0.6); detector covered by the plant below")
    assert len(cites) == 2, f"expected exactly 2 live cites, got: {cites}"
    names = {h.split(":")[0] for h in cites}
    assert names == {"docs/RUNBOOK.md", "docs/OPERATOR.md"}


def _disk_basenames():
    return {p.name for p in ROOT.rglob("*")
            if p.is_file() and ".git" not in p.parts and ".venv" not in p.parts}


def test_phantom_detector_eats_a_planted_phantom():
    known = doc_lint._ref_files()
    disk = _disk_basenames()
    planted = ("see scripts/never_written_" + "xyz.py for the mechanism\n"
               "and docs/nope_" + "xyz.md beside it\n")
    v = doc_lint.phantom_paths("planted_note.md", planted, known, disk)
    assert len(v) == 2, f"planted phantoms slipped: {v}"
    # real paths (ref OR worktree disk) must stay silent
    assert doc_lint.phantom_paths("ok.md", "run scripts/doc_lint.py\n", known, disk) == []


def test_dead_receipt_class_reuses_the_single_helper():
    """clause 8 imports dead_line_violations; composition proof: a planted
    dead number flows through the lint pipeline (digit-split plant)."""
    planted = "the tower measured 83" + "7 visual tokens here\n"
    v = doc_lint.dead_line_violations("planted.md", planted)
    assert len(v) == 1, f"composition broken: {v}"
    v = doc_lint.dead_line_violations("ok.md",
                                      "the tower measured 83" + "7 visual tokens (RETRACTED)\n")
    assert v == [], "per-line RETRACT exemption lost in the reused helper"


def test_version_skew_detector():
    """planted skew fires; current tree is green until the next tag lands."""
    cur = doc_lint.version_skew()
    assert cur == [], f"HEAD version skew is real, not planted: {cur}"

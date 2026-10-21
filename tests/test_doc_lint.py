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


# ---- gap round at 8c2c5f4 (Sammy gaps 1-5, Quinn count-pin + strip spec) ----

def test_gap1_table_row_window_fires_controls_silent():
    """Table rows bind by cell-ADJACENCY: window = whole row. Cell-splitting
    would leave every cell alone and every table inversion would silently
    self-silence — the fake-green trap Sammy named."""
    planted = "| " + "fast | -4" + "x | :808" + "1 |\n"
    v = doc_lint.alias_inversions("planted.md", planted, facts())
    assert len(v) == 1, f"table inversion walked: {v}"
    for ok in ("| fast | 64 tok/tile |\n",                       # true row
               "| " + "detail | -" + "4x | :808" + "1 |\n",      # true row
               "| cost | $2.10 |\n"):                            # no mode
        assert doc_lint.alias_inversions("ok.md", ok, facts()) == [], f"FP row: {ok}"


def test_gap2_portless_file_mode_inversion_fires():
    """Same-class prose as DEPLOY:103 minus the port token: the file marker
    claims its side; the nearest subject must match it."""
    planted = "the -4" + "x file is the fast tower.\n"
    v = doc_lint.alias_inversions("planted.md", planted, facts())
    assert len(v) == 1, f"portless inversion walked: {v}"
    ok = "the -4" + "x file is the detail tower.\n"              # TRUE
    assert doc_lint.alias_inversions("ok.md", ok, facts()) == []


def test_gap4_retract_strip_closes_laundering():
    """Quinn formulation: on RETRACT-marked lines run the detector over the
    line with quoted/backticked spans STRIPPED — a bare assertion survives
    stripping and fires; a cited retraction dissolves."""
    claim = "detail = f1" + "6 = :808" + "0 is the routing.\n"
    assert len(doc_lint.alias_inversions("b1.md", claim, facts())) == 1
    b2 = doc_lint.alias_inversions("b2.md", "RETRACT-context and " + claim, facts())
    assert len(b2) == 1, f"Bare assertion laundered by the word RETRACT: {b2}"
    # the assertive TAIL keeps its binding after the citation is stripped:
    tail = ("RETRACT: the doc said `detail = f1" + "6 = :808" + "0`,"
            " so send OCR traffic to :808" + "0\n")
    assert len(doc_lint.alias_inversions("tail.md", tail, facts())) == 1, \
        "laundered tail walked the strip rule"
    # lawful shape: the dead claim is CITED and no live binding remains —
    # double-quotes here would resurrect the dead-receipt string, so cite
    # retractions in backticks (the shape DEPLOY:102 now uses)
    cite = ("RETRACTED: the old callout said `detail = f1" + "6 = :808" + "0`"
            " \u2014 falsified 2026-10-17\n")
    assert doc_lint.alias_inversions("ok.md", cite, facts()) == []


def test_gap5_alias_keys_resolve_with_binding_context():
    """All six MODE_ALIASES keys are subjects, not just fast/detail — but
    ambiguous English keys (general/ocr/bare 4x) only fire with a wrong-side
    claim or cross-name copula in the window: no wolf, no policy override."""
    for planted in ("mode=ocr routes to :808" + "0\n",
                    "the 4" + "x mode is the fast tower\n"):
        v = doc_lint.alias_inversions("planted.md", planted, facts())
        assert len(v) == 1, f"alias inversion walked: {planted}"
    for ok in ("a general speedup of 2x\n",
               "OCR traffic to :808" + "1 is expected\n",   # TRUE binding
               "mode=detail routes to :808" + "1\n",        # TRUE, F1 control
               "ocr the receipt, then file it\n"):
        assert doc_lint.alias_inversions("ok.md", ok, facts()) == [], f"FP: {ok}"


def test_count_scoped_allowlist_closes_laundering_window():
    """Quinn gap 3: the regime_filter.py allowance is AT MOST 2 hits; a new
    cite in any doc fails the gate on the surplus."""
    two = ["docs/a.md:1: cites 'scripts/regime_filter.py' (phantom)",
           "docs/b.md:9: cites 'scripts/regime_filter.py' (phantom)"]
    assert doc_lint.apply_allowlist(two) == []
    three = two + ["docs/new.md:2: cites 'regime_filter.py' (phantom)"]
    assert any("EXCEEDED" in o for o in doc_lint.apply_allowlist(three))


def test_sinc1_declared_scope_boundary_known_silent():
    """Sammy SINC-1: no per-line rule distinguishes a sincere RETRACT marker
    from an insincere one. This plant stays silent BY DESIGN and the test
    pins that design: RETRACT-marked lines are diff-review surface. If you
    'fix' this, you have started the prose war that kills the gate."""
    sinc = ("RETRACTION OF MY EARLIER DOUBT: `detail = f1" + "6 = :808" + "0`"
            " stands.\n")
    assert doc_lint.alias_inversions("sinc.md", sinc, facts()) == []

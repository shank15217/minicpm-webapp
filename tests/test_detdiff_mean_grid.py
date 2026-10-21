"""qa-40 rev2 (Oct-21): per-site persistence corpus guard.

The fixtures are the attack plants themselves — Sammy A/B/C, Quinn's
annihilation pair and gap-splice, plus the re-pin boundary. Each pins
one law of persistence_corpus():

  * shape is a corpus BREAK, never a diff (and never a crash);
  * adjacency is schedule data, not survivorship-after-filter;
  * sites never diff against each other (in EITHER direction —
    phantom storms AND truth-annihilation were measured in the flat
    ungrouped path);
  * no result may read "checked, clean" when the corpus cannot answer:
    named statuses + messages, never a bare [] and never a flat "ok".

Fixture arithmetic law (Sammy's flag): run == min_run needs >= 9 ROWS
per site — 8 rows give 7 transitions, max run 7. Any plant that wants
an event must visibly carry 9+ rows per site; do not "simplify" to 8.
"""
import io
import json

import pytest
from PIL import Image

import detdiff


def _grid(luma: int, w: int = 1280, h: int = 128) -> dict:
    img = Image.new("L", (w, h), luma)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return detdiff.block_mean_grid(buf.getvalue())


def _row(key: str, hour: int, luma: int, h: int = 128, grid=True,
         minute: int = 0) -> dict:
    r = {"baseline_key": key,
         "ts": f"2026-10-21T{hour:02d}:{minute:02d}:00Z"}
    if grid:
        r["mean_grid"] = _grid(luma, h=h)
    return r


class TestWireFormat:
    def test_real_rig_wire_is_json_string_not_dict(self):
        # The rig stamps mean_grid as a JSON STRING (blockmae wire
        # format). First live-ledger run caught the decoder treating
        # that string as raw b64: some sites raised padding errors,
        # others "decoded" garbage. The corpus must parse the wire form
        # it actually receives.
        rows = [_row("s", h, 100 + 2 * h) for h in range(9)]
        for r in rows:
            r["mean_grid"] = json.dumps(r["mean_grid"])
        s = _site(detdiff.persistence_corpus(rows), "s")
        assert s["status"] == "ok" and s["events"], \
            "JSON-string mean_grid must decode and scan"
        assert all(v == 0 for v in s["skipped"].values())

    def test_bare_b64_string_is_not_legal_wire(self):
        # A grid without cols/rows metadata cannot prove its own shape;
        # it is an undecodable break, never a silent (0,0,N) epoch.
        rows = [_row("s", h, 100) for h in range(9)]
        for r in rows:
            r["mean_grid"] = r["mean_grid"]["b64"]
        s = _site(detdiff.persistence_corpus(rows), "s")
        assert s["usable_rows"] == 0
        assert s["skipped"]["undecodable"] == 9


def _site(out, key):
    return out["sites"][key]


class TestInsufficiencyHouseRule:
    def test_rows_1_to_7_insufficient_with_message_not_bare_empty(self):
        for n in range(8):
            rows = [_row("s", h, 100) for h in range(n)]
            out = detdiff.persistence_corpus(rows)
            if n == 0:
                assert out["sites"] == {} and "nothing checked" in out["message"]
                continue
            s = _site(out, "s")
            assert s["status"] == "insufficient_corpus", f"n={n}"
            assert s["events"] == []
            assert s["usable_rows"] == n and s["need"] == 8
            assert "insufficient corpus" in s["message"], \
                "named insufficiency, never a bare zero-event read"

    def test_eight_rows_ok_but_reports_max_run_possible_seven(self):
        # 8 rows -> 7 transitions -> run==8 arithmetically impossible.
        # The result must SAY so (max_run_possible=7), not silently say 0.
        rows = [_row("s", h, 100 + h * 5) for h in range(8)]
        s = _site(detdiff.persistence_corpus(rows), "s")
        assert s["status"] == "ok" and s["events"] == []
        assert s["max_run_possible"] == 7

    def test_nine_row_ramp_fires_and_only_at_ninth(self):
        # The >=9-rows-per-site law made executable: at 8 rows zero
        # events, at 9 the shallow same-sign editor fires (run==8).
        rows = [_row("s", h, 100 + h * 2) for h in range(8)]
        assert _site(detdiff.persistence_corpus(rows), "s")["events"] == []
        rows.append(_row("s", 8, 116))
        s = _site(detdiff.persistence_corpus(rows), "s")
        assert s["status"] == "ok" and s["events"]
        assert all(e["sign"] == 1 and e["run"] == 8 for e in s["events"])

    def test_oscillation_silent_at_scale(self):
        rows = []
        for i in range(20):
            rows.append(_row("s", i // 2, 100 + (10 if i % 2 else 0),
                             minute=(i % 2) * 30))
        s = _site(detdiff.persistence_corpus(rows), "s")
        assert s["status"] == "ok" and s["events"] == []


class TestShapeLaw:
    def test_A_shorter_later_no_crash_shape_break(self):
        # Sammy A: on the flat path this was IndexError. Now: shape
        # change closes the epoch; the short row starts a fresh one.
        rows = [_row("s", h, 100) for h in range(7)]
        rows.append(_row("s", 7, 100, h=64))
        s = _site(detdiff.persistence_corpus(rows), "s")
        assert s["skipped"]["shape_break"] == 1
        assert s["usable_rows"] == 1
        assert s["status"] == "shape_mismatch"
        assert "NOT a clean result" in s["message"]

    def test_B_shorter_first_no_silent_blindness(self):
        # Sammy B: flat path took n from the SHORT first grid and never
        # scanned cells past it. Now: full rows form their own epoch and
        # EVERY block of their own grid is scanned.
        rows = [_row("s", 0, 100, h=64)]
        rows += [_row("s", h, 100 + h * 2) for h in range(1, 10)]
        s = _site(detdiff.persistence_corpus(rows), "s")
        assert s["skipped"]["shape_break"] == 1
        assert s["usable_rows"] == 9
        g = _grid(100)
        assert s["events"], "post-shape-break ramp must be scanned in full"
        assert len(s["events"]) == g["cols"] * g["rows"], \
            "every block of the FULL grid examined, none blinded"
        assert all(e["epoch_start"] == "2026-10-21T01:00:00Z"
                   for e in s["events"]), "run never reaches back past the break"

    def test_grid_contradicting_own_metadata_is_undecodable_break(self):
        rows = [_row("s", h, 100) for h in range(3)]
        bad = _row("s", 3, 100)
        bad["mean_grid"] = dict(bad["mean_grid"], rows=99)  # lie in metadata
        rows.append(bad)
        s = _site(detdiff.persistence_corpus(rows), "s")
        assert s["skipped"]["undecodable"] == 1


class TestPartitionLaw:
    def test_C_interleaved_no_phantom_storm(self):
        # Sammy C: two sites interleaved as ONE stream produced 1360
        # bogus events on the flat path. Grouped: each site is what it
        # is alone — oscillating, so zero events.
        rows = []
        for h in range(10):
            rows.append(_row("x", h, 100 if h % 2 == 0 else 150))
            rows.append(_row("y", h, 120 if h % 2 == 0 else 70))
        out = detdiff.persistence_corpus(rows)
        assert set(out["sites"]) == {"x", "y"}
        assert _site(out, "x")["events"] == []
        assert _site(out, "y")["events"] == []

    def test_annihilation_pair_truth_survives_grouping(self):
        # Quinn's annihilation: two sites monotonically ramping
        # (+3 and +2), interleaved. Ungrouped: 0 events, status ok —
        # the exact lie. Grouped: BOTH sites report their events.
        # 9 rows per site minimum (the >=9 law, stated above).
        rows = []
        for h in range(9):
            rows.append(_row("up3", h, 100 + 3 * h))
            rows.append(_row("up2", h, 60 + 2 * h))
        out = detdiff.persistence_corpus(rows)
        assert _site(out, "up3")["events"], "ramp +3 must survive interleave"
        assert _site(out, "up2")["events"], "ramp +2 must survive interleave"

    def test_events_tagged_with_their_epoch(self):
        rows = [_row("s", h, 100 + 2 * h) for h in range(9)]
        s = _site(detdiff.persistence_corpus(rows), "s")
        assert all(e["epoch_start"] == "2026-10-21T00:00:00Z"
                   for e in s["events"])


class TestAdjacencyLaw:
    def test_gap_splice_missing_mean_grid_row_breaks_run(self):
        # Quinn: a row present WITHOUT mean_grid (capture failure) used
        # to be silently skipped, splicing two epochs into an apparent
        # unbroken run. 4+5 ramp split by a gridless row: neither side
        # reaches run 8 on its own; the splice would have.
        rows = [_row("s", h, 100 + 2 * h) for h in range(4)]
        rows.append(_row("s", 4, 108, grid=False))       # capture failed
        rows += [_row("s", h, 110 + 2 * (h - 5)) for h in range(5, 10)]
        s = _site(detdiff.persistence_corpus(rows), "s")
        assert s["skipped"]["no_mean_grid"] == 1
        assert s["events"] == [], "gap must break the run, not bridge it"
        assert s["usable_rows"] == 5

    def test_schedule_gap_breaks_run_derived_threshold(self):
        # Rows missing from the LEDGER entirely: 4h gap vs 1h median.
        rows = [_row("s", h, 100 + 2 * h) for h in (0, 1, 2, 3)]
        rows += [_row("s", h, 110 + 2 * (h - 7)) for h in (7, 8, 9, 10, 11)]
        s = _site(detdiff.persistence_corpus(rows), "s")
        assert s["skipped"]["gap_break"] == 1
        assert s["events"] == []

    def test_gap_threshold_travels_with_expected_interval(self):
        # Sammy decision 2: cadence is DATA. 2h-cadence rows with a 7h
        # hole: derived (3x median 2h = 6h) breaks it; a caller that
        # declares a 4h schedule (3x4h=12h) legitimately does not.
        hrs = (0, 2, 4, 6, 13, 15, 17, 19, 21)
        rows = [_row("s", h, 100) for h in hrs]
        s = _site(detdiff.persistence_corpus(rows), "s")
        assert s["skipped"]["gap_break"] == 1
        s2 = _site(detdiff.persistence_corpus(rows, expected_interval_s=4 * 3600),
                   "s")
        assert s2["skipped"]["gap_break"] == 0


class TestRepinBoundary:
    def test_repin_same_shape_breaks_corpus(self):
        # A re-pin keeps the grid shape but changes what block indices
        # MEAN (they are relative to the baseline): baseline_sha change
        # is a corpus break. Pre-pin ramp + post-pin ramp must never
        # concatenate into a run.
        rows = [_row("s", h, 100 + h) for h in range(6)]
        for r in rows:
            r["baseline_sha"] = "aaaa"
        rows += [_row("s", h, 106 + (h - 6)) for h in range(6, 10)]
        for r in rows[6:]:
            r["baseline_sha"] = "bbbb"
        s = _site(detdiff.persistence_corpus(rows), "s")
        assert s["skipped"]["repin_break"] == 1
        assert s["events"] == [], "run never crosses a re-pin boundary"

    def test_baseline_sha_absent_never_breaks_anything(self):
        # Rig control rows carry baseline_sha; consumers that don't must
        # not get phantom breaks.
        rows = [_row("s", h, 100 + 2 * h) for h in range(9)]
        s = _site(detdiff.persistence_corpus(rows), "s")
        assert s["skipped"]["repin_break"] == 0 and s["events"]


class TestScopeUnchanged:
    def test_first_mean_row_never_flips_change_verdict(self):
        base = _png_rgb((10, 20, 30))
        shot_same = _png_rgb((10, 20, 30))
        shot_edit = _png_rgb((250, 250, 250))
        d0 = detdiff.page_diff(base, shot_same, threshold=0.995)
        row = {"baseline_key": "s", "ts": "2026-10-21T00:00:00Z",
               "mean_grid": _grid(100), **d0}
        out = detdiff.persistence_corpus([row])
        assert _site(out, "s")["status"] == "insufficient_corpus"
        assert row["changed"] == detdiff.page_diff(
            base, shot_same, threshold=0.995)["changed"], \
            "first mean-grid row must never flip a change verdict"
        assert detdiff.page_diff(base, shot_edit,
                                 threshold=0.995)["changed"] is True

    def test_persistence_events_rejects_mismatched_direct_call(self):
        # Direct callers get an explicit error, never IndexError or a
        # silently truncated field of view.
        with pytest.raises(ValueError):
            detdiff.persistence_events([[1, 2, 3], [1, 2]])


def _png_rgb(color) -> bytes:
    img = Image.new("RGB", (1280, 128), color)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()

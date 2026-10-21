"""qa-40 (Oct-21): mean-grid corpus guard — persistence_corpus().

The first mean-grid rows change the ledger row SHAPE; every consumer must
distinguish "insufficient corpus" from "checked, nothing found". Law under
test (eb7d0bb scope): mean grids are persistence-only and NEVER flip a
change verdict; MAE grid stays the ceiling source.
"""
import base64
import io

import pytest
from PIL import Image

import detdiff


def _grid(luma: int, w: int = 1280, h: int = 128) -> dict:
    img = Image.new("L", (w, h), luma)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return detdiff.block_mean_grid(buf.getvalue())


class TestPersistenceCorpusGuard:
    def test_rows_1_to_7_return_insufficient_not_empty_list(self):
        for n in range(8):
            rows = [{"mean_grid": _grid(100)} for _ in range(n)]
            out = detdiff.persistence_corpus(rows)
            assert out["status"] == "insufficient_corpus", f"n={n}"
            assert out["events"] == []
            assert out["rows"] == n and out["need"] == 8
            assert "insufficient corpus" in out["message"], \
                "honest output is a human-readable insufficiency, " \
                "never a bare zero-event list"

    def test_first_mean_row_alone_never_claims_persistence(self):
        out = detdiff.persistence_corpus([{"mean_grid": _grid(255)}])
        assert out["status"] == "insufficient_corpus"
        assert out["events"] == []

    def test_mixed_shapes_count_only_mean_rows(self):
        rows = [{"page_sim": 1.0} for _ in range(30)] + [{"mean_grid": _grid(90)}]
        out = detdiff.persistence_corpus(rows)
        assert out["status"] == "insufficient_corpus" and out["rows"] == 1

    def test_eight_quiet_rows_ok_with_zero_events(self):
        rows = [{"mean_grid": _grid(100)} for _ in range(8)]
        out = detdiff.persistence_corpus(rows)
        assert out["status"] == "ok" and out["events"] == []

    def test_signed_run_fires_eight_same_sign(self):
        # A steady same-sign editor (Sammy's shallow-editor class):
        # +2 luma per row; the 9th row completes run==8 -> event.
        rows = [{"mean_grid": _grid(100 + i * 2)} for i in range(9)]
        out = detdiff.persistence_corpus(rows)
        assert out["status"] == "ok"
        assert out["events"], "steady same-sign editor must fire"
        ev = out["events"][0]
        assert ev["sign"] == 1 and ev["run"] == 8

    def test_oscillation_stays_silent_at_scale(self):
        rows = [{"mean_grid": _grid(100 + (10 if i % 2 else 0))}
                for i in range(20)]
        out = detdiff.persistence_corpus(rows)
        assert out["status"] == "ok" and out["events"] == []

    def test_grid_decoded_from_b64_string_or_dict(self):
        g = _grid(100)
        rows_dict = [{"mean_grid": g}]
        rows_b64 = [{"mean_grid": g["b64"]}] * 8
        assert detdiff.persistence_corpus(rows_dict)["rows"] == 1
        assert detdiff.persistence_corpus(rows_b64)["status"] == "ok"


class TestFirstMeanRowNeverFlipsVerdict:
    def test_verdict_identical_with_mean_grid_attached(self):
        base = _png_rgb((10, 20, 30))
        shot_same = _png_rgb((10, 20, 30))
        shot_edit = _png_rgb((250, 250, 250))
        d0 = detdiff.page_diff(base, shot_same, threshold=0.995)
        d1 = detdiff.page_diff(base, shot_same, threshold=0.995)
        # A row that carries mean_grid for the FIRST time (shape change):
        row = {"mean_grid": _grid(100), **d0}
        corpus = detdiff.persistence_corpus([row])
        assert corpus["status"] == "insufficient_corpus"
        assert row["changed"] == d1["changed"], \
            "first mean-grid row must never flip a change verdict"
        d_edit = detdiff.page_diff(base, shot_edit, threshold=0.995)
        assert d_edit["changed"] is True
        assert detdiff.persistence_corpus(
            [{"mean_grid": _grid(1)}])["events"] == [], \
            "one mean row is not evidence of anything"


def _png_rgb(color) -> bytes:
    img = Image.new("RGB", (1280, 128), color)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()

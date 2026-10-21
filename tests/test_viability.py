"""Viability-gate report tests (Quinn final spec + Sammy content half).

Every graduation criterion gets a plant AND a counter-plant: a gate that
only passes good sites proves nothing; one that also REJECTS the known
pathologies (amazon's 1-in-3 shape flip, regime drift, blank bands) and
SEES the known content pathology (a DYK-style per-fire randomizer) is
the gate. All synthetic grids here use the real JSON-string wire.
"""
import json

import detdiff
from test_detdiff_mean_grid import _grid, _row


def _fire(key, hour, luma, h=128, regime="prescroll-4", blank=()):
    r = _row(key, hour, luma, h=h)
    r["mean_grid"] = json.dumps(r["mean_grid"])     # real wire form
    r["capture_regime"] = regime
    r["capture_sanity"] = json.dumps({"blank_bands": list(blank)})
    r["blockmae"] = json.dumps({"g64": {"b64": "", "size": 64,
                                        "cols": 20, "rows": 2}})
    return r


def _static(key, n=24, luma=100):
    return [_fire(key, h, luma) for h in range(n)]


class TestShapeSide:
    def test_static_site_graduates(self):
        out = detdiff.viability_report(_static("calm"))
        s = out["sites"]["calm"]
        assert s["graduates_shape"] and out["pass"] == ["calm"] and not out["fail"]
        assert s["mode_share"] == 1.0 and s["shape_flips"] == 0

    def test_amazon_single_transient_flip_still_passes_at_threshold(self):
        # The 16:00 collapse class: 23/24 at the 46-row shape, 1 short
        # fire. 23/24 == the threshold — passes, flip counted and visible.
        rows = _static("shop", n=23) + [_fire("shop", 23, 100, h=64)]
        s = detdiff.viability_report(rows)["sites"]["shop"]
        assert s["graduates_shape"]
        assert s["shape_flips"] == 1 and s["mode_share"] == round(23 / 24, 4)

    def test_amazon_flip_rate_fails_the_gate(self):
        # Quinn's validation: at the observed 1-in-3 flip rate the site
        # fails on day one — before any baseline ceremony.
        rows = []
        for h in range(24):
            rows.append(_fire("shop", h, 100, h=(64 if h % 3 == 2 else 128)))
        out = detdiff.viability_report(rows)
        assert "shop" in out["fail"]
        assert any("mode_share" in r for r in out["fail"]["shop"])
        assert out["sites"]["shop"]["shape_flips"] >= 10

    def test_too_few_fires_never_graduates(self):
        out = detdiff.viability_report(_static("new", n=8))
        assert "new" in out["fail"] and any("< 24" in r for r in out["fail"]["new"])


class TestRegimeAndBlank:
    def test_regime_drift_is_autofail(self):
        rows = _static("site", n=23)
        rows.append(_fire("site", 23, 100, regime="prescroll-3"))
        out = detdiff.viability_report(rows)
        assert "site" in out["fail"]
        assert any("regime drift" in r for r in out["fail"]["site"])

    def test_blank_bands_below_share_fails(self):
        rows = _static("site", n=22)
        rows += [_fire("site", h, 100, blank=(5, 6)) for h in (22, 23)]
        out = detdiff.viability_report(rows)
        assert "site" in out["fail"]
        assert any("blank_bands" in r for r in out["fail"]["site"])

    def test_blank_bands_within_allowance_pass(self):
        rows = _static("site", n=23)
        rows.append(_fire("site", 23, 100, blank=(9,)))   # 23/24 empty
        assert detdiff.viability_report(rows)["sites"]["site"]["graduates_shape"]


class TestContentFloorSammyHalf:
    def test_dyk_style_randomizer_is_seen_not_gating(self):
        # The hole Sammy named: a shape-stable, content-randomized site
        # walks ALL shape criteria. The content half must surface its
        # random block as a pre-registered oscillator — reported, not
        # a gate failure (it informs waivers/ceilings).
        import random
        rng = random.Random(7)
        rows = []
        for h in range(24):
            r = _fire("portal", h, 100)
            g = json.loads(r["mean_grid"])
            vals = bytearray(g["cols"] * g["rows"])
            meta_cols = g["cols"]
            for i in range(len(vals)):
                vals[i] = 100 + (rng.randint(-12, 12) if i % meta_cols == 7
                                 else 0)
            import base64 as _b64
            g["b64"] = _b64.b64encode(bytes(vals)).decode()
            r["mean_grid"] = json.dumps(g)
            rows.append(r)
        out = detdiff.viability_report(rows)
        s = out["sites"]["portal"]
        assert s["graduates_shape"], "shape side stays clean — content is separate"
        blocks = {o["block"] for o in s["oscillators"]}
        col7 = {b for b in range(40) if b % g["cols"] == 7}
        assert blocks and blocks <= col7, \
            "the random column shows up as the oscillator, nothing else does"

    def test_clean_site_reports_no_oscillators(self):
        out = detdiff.viability_report(_static("calm"))
        assert out["sites"]["calm"]["oscillators"] == []

    def test_one_real_edit_is_not_an_oscillator(self):
        # A single known edit moves one block once — sign-run material,
        # not noise. Flip rate needs >=4 moving steps; one step reports
        # nothing.
        rows = _static("site", n=10)
        r = _fire("site", 10, 100)
        g = json.loads(r["mean_grid"])
        import base64 as _b64
        vals = bytearray([100] * (g["cols"] * g["rows"]))
        vals[3] = 180
        g["b64"] = _b64.b64encode(bytes(vals)).decode()
        r["mean_grid"] = json.dumps(g)
        rows.append(r)
        out = detdiff.viability_report(rows[:11])
        assert out["sites"]["site"]["oscillators"] == []


class TestOnRealWire:
    def test_frozen_snapshot_reports_honestly_not_graduates_yet(self):
        # The 18:00-era real rows: 1 fire per site — the report must say
        # the arithmetic truth (fires 1 < 24) for every site, no crash,
        # no site marked graduate.
        import json as _j
        from pathlib import Path
        rows = [_j.loads(l) for l in (Path(__file__).parent / "fixtures" /
                "control_snapshot.jsonl").read_text().splitlines() if l.strip()]
        out = detdiff.viability_report(rows)
        assert out["pass"] == [] and len(out["fail"]) >= 8
        for key, reasons in out["fail"].items():
            assert any("< 24" in r for r in reasons)

"""GRIDWIN parity for the ceiling lane (Sammy ruling r5).

The regime string must describe EVERY grid on the row: g64/t16 get the
same top-anchored window as the mean grid, AND fit refuses any per-key
cohort whose g64 shape histogram is not a single shape — exclusion with
a named blocker, never a cellwise blend between different page regions.
Amazon's collapses (min(H,h) semantics) supply the mixed-shape material
for free; here it is planted on purpose.
"""
import base64
import io
import json
import sys
from pathlib import Path

import pytest
from PIL import Image

import detdiff

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "calibration"))
import fit as fitmod  # noqa: E402


def _png(w: int, h: int, shade: int = 128) -> bytes:
    im = Image.new("L", (w, h), shade)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


# --- window parity on the MAE side ---

def test_mae_sig_windowed_shape_deterministic_for_tall_pages():
    base = _png(1280, 5000)
    live_a = _png(1280, 4096)
    live_b = _png(1280, 6100)
    ga = detdiff.block_mae_sig(base, live_a, window_px=2560)
    gb = detdiff.block_mae_sig(base, live_b, window_px=2560)
    assert (ga["cols"], ga["rows"]) == (gb["cols"], gb["rows"]) == (20, 40)


def test_mae_sig_collapse_still_differs_under_window():
    base = _png(1280, 5000)
    tall = detdiff.block_mae_sig(base, _png(1280, 4800), window_px=2560)
    collapse = detdiff.block_mae_sig(base, _png(1280, 896), window_px=2560)
    assert collapse["rows"] == 14 != tall["rows"]      # never laundered


def test_mae_shadow_both_grids_windowed():
    sh = detdiff.block_mae_shadow(_png(1280, 5000), _png(1280, 5010),
                                  window_px=2560)
    assert (sh["g64"]["cols"], sh["g64"]["rows"]) == (20, 40)
    # t16 fine grid is windowed too: coordinate rows stay < 2560//16=160
    assert all(coord[1] < 160 for coord in sh["t16"]) or not sh["t16"]


def test_rig_and_fit_regime_constants_move_together():
    rig_src = (Path(__file__).resolve().parent.parent
               / "calibration" / "rig.py").read_text()
    rig_const = [ln for ln in rig_src.splitlines()
                 if ln.startswith("CAPTURE_REGIME")][0]
    rig_val = rig_const.split('"')[1]
    assert rig_val == fitmod.CURRENT_REGIME


# --- fit single-shape guard ---

def _row(key: str, ts: str, cols: int, rows: int) -> str:
    g64 = {"size": 64, "cols": cols, "rows": rows,
           "b64": base64.b64encode(bytes(cols * rows)).decode()}
    return json.dumps({
        "ts": ts, "capture_regime": fitmod.CURRENT_REGIME,
        "baseline_key": key, "changed": False,
        "blockmae": json.dumps({"g64": g64, "t16": [], "floor16": 0}),
    })


def _cohort(tmp_path):
    days = [f"2026-11-0{d}T0{d}:00:00.000000Z" for d in range(1, 7)]
    lines = [_row("cleankey", ts, 20, 40) for ts in days]
    # same regime, two shapes: gridwin collapse inside a quiet cohort
    lines.append(_row("mixkey", days[0], 20, 40))
    lines.append(_row("mixkey", days[1], 20, 14))     # collapse below H
    lines.append(_row("mixkey", days[2], 20, 40))
    p = tmp_path / "cohort.jsonl"
    p.write_text("\n".join(lines) + "\n")
    return p


def test_fit_excludes_mixed_shape_cohort_with_named_blocker(tmp_path):
    out = fitmod.fit(_cohort(tmp_path), min_rows=2, k=3.0, floor=8,
                     cap=48, min_days=5)
    blockers = out["params"]["excluded"]
    assert any(b.startswith("mixed_g64_shape:mixkey") for b in blockers)
    ceil_keys = set(out["ceilings"])
    assert "mixkey" not in ceil_keys            # excluded, never blended
    # clean cohort still fits (guard excludes, it does not veto the world)
    assert any("cleankey" in k for k in ceil_keys) or out["ceilings"]

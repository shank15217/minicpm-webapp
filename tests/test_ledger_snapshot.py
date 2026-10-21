"""Frozen real-ledger fixture (Sammy's mechanization of Quinn's doctrine,
Oct-21): synthetic plants proved the detector LAWS; the production WIRE
then failed differently and quieter (JSON-string mean_grid decoded as raw
b64 into (0,0,N) garbage). Neither fixture kind alone is sufficient — so
this file pins the exact bytes-shaped contract of real rig rows.

Source: /root/mpw-calibration/control.jsonl on pvellm, frozen
2026-10-21 at three fires: 04:00 + 10:00 UTC (control_batch.py era:
blockmae, no mean_grid) and 18:00 UTC (repo-rig era: mean_grid +
capture_surface). 24 rows, 8 sites, live file untouched, no secrets —
URLs are public targets, values are shas and grids.

If a schema change lands — re-stringifying a field, renaming cols/rows,
moving mean_grid, changing the blockmae sig shape — these tests fail at
COMMIT time, not at ledger-run time. Keep the snapshot frozen; refresh
deliberately (new file name, dated) so drift is a decision, not an
accident. Consumer guidance (Sammy): alert on the RATIO of skipped rows,
not merely on presence of events — skip counters make bad input look
plausible at a glance.
"""
import base64
import json
from pathlib import Path

import detdiff

SNAP = Path(__file__).parent / "fixtures" / "control_snapshot.jsonl"


def _rows():
    return [json.loads(l) for l in SNAP.read_text().splitlines() if l.strip()]


class TestWireContract:
    def test_snapshot_is_parseable_with_provenance_keys(self):
        rows = _rows()
        assert len(rows) == 24 and len({r["baseline_key"] for r in rows}) == 8
        for r in rows:
            for k in ("ts", "baseline_key", "kind", "capture_regime",
                      "app_commit", "baseline_sha", "blockmae"):
                assert k in r, f"row {r.get('ts')} missing {k}"

    def test_mean_grid_wire_shape(self):
        # THE tripwire: mean_grid is a JSON STRING wrapping a dict with
        # kind/size/cols/rows/b64, and b64 length == cols*rows exactly.
        n = 0
        for r in rows_with_mean():
            n += 1
            meta = r["mean_grid"]
            assert isinstance(meta, str), "rig wire is JSON string, not dict"
            meta = json.loads(meta)
            assert meta["kind"] == "block_mean" and meta["size"] == 64
            raw = base64.b64decode(meta["b64"], validate=True)
            assert meta["cols"] * meta["rows"] == len(raw)
        assert n == 8, "18:00 fire must contribute exactly 8 mean-grid rows"

    def test_blockmae_wire_shape(self):
        for r in _rows():
            sig = r["blockmae"]
            assert isinstance(sig, str)
            sig = json.loads(sig)
            assert "g64" in sig and "b64" in sig["g64"]
            base64.b64decode(sig["g64"]["b64"], validate=True)

    def test_capture_surface_present_on_repo_rig_era_rows(self):
        rig_era = [r for r in _rows() if r.get("mean_grid")]
        assert rig_era and all("capture_surface" in r for r in rig_era)


class TestRealLedgerFeedsGuardTruthfully:
    def test_no_row_is_undecodable(self):
        # Schema-drift tripwire through the consumer: if any future wire
        # change makes real rows unparsable, THIS goes red at commit time.
        out = detdiff.persistence_corpus(_rows())
        for key, s in out["sites"].items():
            assert s["skipped"]["undecodable"] == 0, \
                f"{key}: real rows became undecodable — wire drift"

    def test_honest_states_on_the_frozen_fire(self):
        out = detdiff.persistence_corpus(_rows())
        assert len(out["sites"]) == 8
        for key, s in out["sites"].items():
            assert s["status"] == "insufficient_corpus"
            assert s["usable_rows"] == 1          # the 18:00 fire only
            assert s["events"] == []
            assert "insufficient corpus" in s["message"]

    def test_legacy_rows_counted_as_boundaries_not_crashes(self):
        out = detdiff.persistence_corpus(_rows())
        total_no_mg = sum(s["skipped"]["no_mean_grid"]
                          for s in out["sites"].values())
        assert total_no_mg == 16                  # 04:00 + 10:00 fires


def rows_with_mean():
    out = []
    for r in _rows():
        if r.get("mean_grid"):
            out.append(r)
    return out

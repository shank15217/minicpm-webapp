"""Sensitivity positive control for the published persistence number (Sammy).

The clause-9 artifact publishes "zero sustained changes so far" on the real
corpus. A zero is only a measurement if the detector is demonstrably ARMED on
the same wire it read: identical real rows stay silent, a planted sustained
ramp on those same real payloads FIRES. A test that passes on absence of bad
outcome is not a pass unless a positive control shows the detector had
ammunition — this file is that control, committed so the sensitivity claim is
third-party re-runnable without any reviewer's scratch kernel.

Plants run against `persistence_corpus` (the production entry point, the law
lives inside it) using the REAL wire form: mean_grid as a JSON string
{"kind,size,cols,rows,b64} per control_snapshot.jsonl. Payload bytes come
from the frozen real-ledger fixture; only timestamps are synthesized.
"""

import base64
import copy
import datetime
import json
import pathlib

import pytest

import detdiff

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "control_snapshot.jsonl"


def _rows():
    return [json.loads(l) for l in FIXTURE.read_text().splitlines() if l.strip()]


def _proto():
    """One real fhlb mean-grid row to clone payloads from."""
    for r in _rows():
        if r.get("baseline_key", "").startswith("8a2620e6") and r.get("mean_grid"):
            return r
    raise AssertionError("fixture lost its fhlb mean-grid row")


def _bumped(proto, add, t):
    """Clone a real ledger row with every grid byte shifted by `add`
    (clamped), ts replaced. Wire form preserved: JSON *string* grid."""
    r2 = copy.deepcopy(proto)
    d = json.loads(r2["mean_grid"]) if isinstance(r2["mean_grid"], str) else r2["mean_grid"]
    b = bytes(min(255, x + add) for x in base64.b64decode(d["b64"]))
    d["b64"] = base64.b64encode(b).decode()
    r2["mean_grid"] = json.dumps(d)
    r2["ts"] = t
    return r2


def _iso(epoch):
    return datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc).isoformat()


T0 = 1_761_000_000


class TestSensitivityPositiveControl:
    def setup_method(self):
        self.proto = _proto()
        self.key = self.proto["baseline_key"]

    def _corpus(self, rows):
        return detdiff.persistence_corpus(rows)["sites"][self.key]

    def test_identical_real_rows_stay_silent(self):
        # Control leg: nine hourly clones of the SAME payloads must produce
        # zero events while the corpus reports ok (not insufficiency).
        rows = [_bumped(self.proto, 0, _iso(T0 + i * 3600)) for i in range(9)]
        site = self._corpus(rows)
        assert site["status"] == "ok"
        assert site["events"] == []

    def test_planted_ramp_fires_on_real_wire(self):
        # Sensitivity leg: +3/hour for nine consecutive fires is a run of 8
        # same-sign transitions each >= min_mag — the shipped law MUST fire.
        # If this ever goes silent, the published "zero events" number is no
        # longer a measurement, it is an inert instrument.
        rows = [_bumped(self.proto, 3 * i, _iso(T0 + i * 3600)) for i in range(9)]
        site = self._corpus(rows)
        assert site["status"] == "ok"
        assert len(site["events"]) >= 1
        assert any(ev.get("run") == 8 for ev in site["events"])

    def test_duty_cycle_breaks_the_run(self):
        # Documented floor (velocity class, see test_persistence_shadow): a
        # rest row with no mean_grid SPLICES nothing — it breaks adjacency.
        # Two 8-row half-ramps around a gap must NOT form a run of 8. This
        # stays silent by design; if a future law "fixes" it, that is a new
        # cumulative-layer decision, not this file's job.
        rows = [_bumped(self.proto, 3 * i, _iso(T0 + i * 3600)) for i in range(8)]
        rows.append({**copy.deepcopy(self.proto), "mean_grid": None,
                     "ts": _iso(T0 + 8 * 3600)})
        rows += [_bumped(self.proto, 24 + 3 * i, _iso(T0 + (9 + i) * 3600))
                 for i in range(8)]
        site = self._corpus(rows)
        assert site["events"] == []
        # and honestly report the splice, not launder it:
        assert site["skipped"]["no_mean_grid"] >= 1

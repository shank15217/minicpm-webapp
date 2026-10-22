"""Shadow-law acceptance plants (Sammy duty-cycle, Quinn sawtooth,
Sammy's fifth sub-velocity plant, Quinn's term-label consumer law).

Every plant asserts the SHIPPED run law's behavior too, so the file is
a permanent record of WHICH hole each term closes — and the
sub-velocity plant documents the floor of the whole law FAMILY as
known-silent out-of-scope, to stop anyone later "fixing" the velocity
detector into eating the cumulative layer's (v0.7 ceilings) output.
"""
import json

import detdiff


def _streams(values_by_block, n=None):
    """grids from {block: [v0, v1, ...]} — all blocks same length."""
    n = n or len(next(iter(values_by_block.values())))
    nblocks = max(values_by_block) + 1
    grids = []
    for t in range(n):
        g = [100] * nblocks
        for bi, seq in values_by_block.items():
            g[bi] = seq[t]
        grids.append(g)
    return grids


def _duty(v0=100, steps=17):
    # +4 every OTHER row: 17 rows, 16 deltas, nz=8, total +32
    return [v0 + 4 * ((t + 1) // 2) for t in range(steps)]


def _saw(v0=100, rows=33):
    v, seq = v0, [v0]
    for t in range(rows - 1):
        v += 3 if t % 2 == 0 else -2
        seq.append(v)
    return seq


class TestDutyCycleSammy:
    def test_duty_cycle_shipped_blind_shadow_fires(self):
        grids = _streams({0: _duty()})
        assert detdiff.persistence_events(grids) == []      # shipped: blind
        ev = detdiff.persistence_events_ratio_net(grids)
        by = {e["term"] for e in ev}
        assert {"ratio", "net"} <= by                        # both close it
        r = [e for e in ev if e["term"] == "ratio"][0]
        assert r["pos"] == 8 and r["neg"] == 0 and r["sign"] == 1

    def test_plain_editor_unchanged_all_terms_agree(self):
        grids = _streams({0: [100 + 2 * t for t in range(20)]})
        assert len(detdiff.persistence_events(grids)) == 1   # shipped fires
        ev = detdiff.persistence_events_ratio_net(grids)
        assert {"ratio", "net"} <= {e["term"] for e in ev}


class TestOscillatorSilence:
    def test_oscillator_silent_all_three(self):
        seq = [100 + (5 if t % 2 else 0) for t in range(24)]
        grids = _streams({0: seq})
        assert detdiff.persistence_events(grids) == []
        assert detdiff.persistence_events_ratio_net(grids) == []

    def test_net_silent_by_construction_even_biased_mildly(self):
        # asymmetric flicker +6/-5: net +1 per pair = +8 per 16 window —
        # this IS the residual cost Quinn stated: biased oscillation
        # becomes a net-term suspicion event (accepted; shadow decides).
        seq, v = [100], 100
        for t in range(32):
            v += 6 if t % 2 == 0 else -5
            seq.append(v)
        ev = detdiff.persistence_events_ratio_net(_streams({0: seq}))
        assert [e["term"] for e in ev] == ["net"]   # ratio 0.5: silent


class TestSawtoothQuinn:
    def test_biased_sawtooth_fires_net_only(self):
        grids = _streams({0: _saw()})
        assert detdiff.persistence_events(grids) == []       # shipped blind
        ev = detdiff.persistence_events_ratio_net(grids)
        assert [e["term"] for e in ev] == ["net"]
        assert ev[0]["net_sum"] == 8 and ev[0]["sign"] == 1


class TestSubVelocitySammyFifthPlant:
    def test_sub_velocity_known_silent_out_of_scope(self):
        # +1 every fourth row: +6 net/day — silent under run AND under
        # the full ratio+net law at min_mag=2. KNOWN-SILENT BY DESIGN:
        # cumulative-layer (per-block ceiling vs baseline) prey. If this
        # plant ever starts firing, someone changed the family's floor —
        # that is a layer-boundary violation, not a win.
        seq = [100 + t // 4 for t in range(40)]
        grids = _streams({0: seq})
        assert detdiff.persistence_events(grids) == []
        assert detdiff.persistence_events_ratio_net(grids) == []
        assert "OUT OF SCOPE" in detdiff.persistence_shadow([])["scope_note"]


class TestConsumerLaws:
    def test_every_event_carries_term_label(self):
        grids = _streams({0: _duty() + [100] * 10})
        for e in detdiff.persistence_events_ratio_net(grids):
            assert e["term"] in ("ratio", "net")

    def test_mismatch_raises_not_crashes(self):
        try:
            detdiff.persistence_events_ratio_net([[1, 2], [3]])
            assert False, "expected ValueError"
        except ValueError:
            pass


class TestShadowTable:
    def _rows(self, key, seqs, start_hour=0):
        rows = []
        for t, vals in enumerate(seqs):
            g = [100] * 4
            for bi, v in vals.items():
                g[bi] = v
            import base64
            rows.append({"ts": f"2026-10-22T{(start_hour + t) % 24:02d}:00:00Z",
                         "baseline_key": key,
                         "mean_grid": json.dumps(
                             {"cols": 2, "rows": 2,
                              "b64": base64.b64encode(bytes(g)).decode()})})
        return rows

    def test_interleaved_sites_stay_partitioned(self):
        a = self._rows("siteA", [{0: 100 + 4 * ((t + 1) // 2)} for t in range(17)])
        b = self._rows("siteB", [{0: 100} for _ in range(17)])
        out = detdiff.persistence_shadow(a + b)              # interleaved ok
        assert out["sites"]["siteA"]["events"]["ratio"]
        for term in ("run", "ratio", "net"):
            assert out["sites"]["siteB"]["events"][term] == []

    def test_scope_note_ships_with_every_table(self):
        out = detdiff.persistence_shadow(self._rows("s", [{0: 100}] * 3))
        assert "OUT OF SCOPE" in out["scope_note"]
        assert set(out["sites"]["s"]["events"]) == {"run", "ratio", "net"}

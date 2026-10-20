"""Fixture harness for the quiet-gate acceptance (mirrors Quinn's planned
mechanical check + Sammy's sub-threshold block-mover)."""
import base64
import json
import subprocess  # noqa: F401
import sys

import pathlib as _pl
sys.path.insert(0, str(_pl.Path(__file__).resolve().parent.parent))
import fit  # noqa: E402

COLS, ROWS = 20, 12
NBYTES = COLS * ROWS


def row(ts, key, vals, changed):
    return json.dumps({
        "ts": ts, "capture_regime": "prescroll-4", "baseline_key": key,
        "changed": changed,
        "blockmae": json.dumps({"g64": {"size": 64, "cols": COLS, "rows": ROWS,
                                        "b64": base64.b64encode(bytes(vals)).decode()}}),
    })


def days(n, h=6):
    base = 1  # day counter -> ISO-ish stamps (parseable, 5+ days span)
    return ["2026-10-%02dT%02d:00:00Z" % (12 + (i * h) // 24, (i * h) % 24)
            for i in range(n)]


quiet = [row(t, "K1", [5] * 8 + [10] * 2 + [3] * (NBYTES - 10), False)
         for t in days(56)]  # 14 days at 6h

planted = [
    row("2026-10-13T03:00:00Z", "K1", [200] * NBYTES, True),            # changed=True drama
    row("2026-10-14T03:00:00Z", "K1", [5] * 100 + [180] + [3] * (NBYTES - 101), False),  # sub-threshold block mover
    row("2026-10-20T03:51:30Z", "K1", [90] * NBYTES, False),            # quarantined window
]

with open("/tmp/qc_clean.jsonl", "w") as f:
    f.write("\n".join(quiet))
with open("/tmp/qc_planted.jsonl", "w") as f:
    f.write("\n".join(quiet + planted))

clean = fit.fit(__import__("pathlib").Path("/tmp/qc_clean.jsonl"), 48, 2.0, 24, 255)
planted_fit = fit.fit(__import__("pathlib").Path("/tmp/qc_planted.jsonl"), 48, 2.0, 24, 255)

c, p = clean["ceilings"]["K1"], planted_fit["ceilings"]["K1"]
print("clean obs:", c["observations"], "| planted-run obs:", p["observations"])
print("excluded tally (planted run):", clean["params"]["excluded"], "vs", planted_fit["params"]["excluded"])
print("ceilings byte-identical despite planted rows:", c["b64"] == p["b64"])
assert c["observations"] == p["observations"], "planted rows leaked into count"
assert c["b64"] == p["b64"], "planted rows moved a ceiling"
print("PASS: changed rows, sub-threshold block-movers, and quarantined windows contribute ZERO to ceilings")

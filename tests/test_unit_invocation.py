"""D10 regression gate (QA-REPORT-10): the shipped systemd monitor path must
run the exporter, not just the monitor.

The v1.0.0 bug was not math and not the purity filter — export_prom.py is
correct — it was that NO shipped artifact invoked export_prom.py on the
systemd path. `mpw_scrape_ok=1` vouched for a 7-day-old file because the
only caller lived in scripts/run_daily.sh (the clone-and-run path) while
prod runs the unit file. Doctrine (Quinn): a fixture proves a function,
only the unit file proves a product. These static asserts pin the INVOCATION
PATH so the "calendar, not code" class of bug cannot ship again:

  1. the unit's ExecStart runs monitorlib.py run AND scripts/export_prom.py;
  2. the exporter runs unconditionally after the monitor (`;` not `&&`) —
     a dead monitor must still refresh the staleness gauge, which is the
     one signal you consult during an outage;
  3. a failing exporter must not mask the monitor's exit code (no bare
     `exit 0`; the chain preserves rc).
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
UNIT = ROOT / "deploy" / "systemd" / "minicpm-monitor.service"


def _execstart() -> str:
    text = UNIT.read_text()
    lines, cont = [], False
    for line in text.splitlines():
        if cont:
            lines.append(line)
            cont = line.endswith("\\")
        elif line.startswith("ExecStart="):
            lines.append(line[len("ExecStart="):])
            cont = line.endswith("\\")
    assert lines, "minicpm-monitor.service lost its ExecStart"
    return " ".join(lines)


def test_monitor_unit_invokes_export_prom():
    cmd = _execstart()
    assert "monitorlib.py run" in cmd, "monitor step vanished from ExecStart"
    assert "export_prom.py" in cmd, (
        "D10 regression: shipped monitor unit never runs the exporter — "
        "/metrics will freeze while mpw_scrape_ok stays 1")


def test_exporter_runs_even_when_monitor_step_fails():
    cmd = _execstart()
    mon = cmd.index("monitorlib.py run")
    exp = cmd.index("export_prom.py")
    assert mon < exp, "exporter must run AFTER the monitor pass"
    between = cmd[mon:exp]
    assert "&&" not in between.split("runs.db")[-1], (
        "exporter chained with && — a failed monitor must still refresh "
        "the freshness gauge (blind must not masquerade as quiet)")


def test_exporter_failure_does_not_mask_monitor_exit_code():
    cmd = _execstart()
    assert re.search(r"exit\s+\$\{?rc\}?", cmd), (
        "ExecStart must preserve the monitor's exit code through the "
        "exporter tail (rc=$? ... exit $rc); bare 'exit 0' would make a "
        "dead monitor look green to systemd")


def test_run_daily_header_matches_unit_reality():
    """D10a: the header used to PROMISE the unit ran the exporter while it
    didn't. Pin both sides of the claim so they can never diverge silently."""
    header = (ROOT / "scripts" / "run_daily.sh").read_text()
    header_block = header.split("set -eu")[0]
    promises_unit_export = "export_prom" in header_block
    unit_runs_export = "export_prom.py" in _execstart()
    assert promises_unit_export == unit_runs_export, (
        "run_daily.sh header and the unit file disagree about whether the "
        "systemd path exports metrics — docs that name a wiring the units "
        "don't have are folklore with a comment on it")

"""Deterministic pins for monitorlib (W11 closure). Receipts by path:
claims about these tests cite tests/test_monitor.py @ <sha>. No network, no
pvellm knowledge; the one transport probe targets 127.0.0.1:1 (closed port,
instant refusal on any host).
"""
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import monitorlib as M

NOW = datetime(2026, 10, 16, 12, 0, 0, tzinfo=timezone.utc)


def ts(**kw):
    return (NOW - timedelta(**kw)).isoformat(timespec="seconds")


# --- requirement 2: deadline logic is pure and pinnable -------------------

def test_is_stale_no_row_is_stale():
    assert M.is_stale(None, NOW, 360, 90) is True


def test_is_stale_fresh_row_not_stale():
    assert M.is_stale(ts(minutes=10), NOW, 360, 90) is False


def test_is_stale_beyond_cadence_plus_grace():
    assert M.is_stale(ts(hours=8), NOW, 360, 90) is True       # 8h > 4.5h
    assert M.is_stale(ts(hours=4, minutes=20), NOW, 360, 90) is False  # inside grace


def test_is_stale_unparseable_or_naive_timestamps():
    assert M.is_stale("not-a-date", NOW, 60, 10) is True       # untrustworthy == stale
    naive = "2026-10-16T11:30:00"                              # naive treated as UTC: fresh
    assert M.is_stale(naive, NOW, 60, 10) is False


# --- requirement 1: terminal rows, latest wins -----------------------------

def test_heartbeat_table_and_terminal_latest(tmp_path):
    db = str(tmp_path / "runs.db")
    con = M.connect(db)                       # creates heartbeat table on demand
    M.record_heartbeat(con, "s1", "CHECK_FAILED", 1.2, 7, "status=CHECK_FAILED")
    M.record_heartbeat(con, "s1", "OK", 2.0, 9, "sim=0.99")
    row = M.latest_heartbeat(con, "s1")
    assert row[1] == "OK" and row[3] == 9
    # one row per run — nothing written at start: table has exactly the two
    # COMPLETION rows, no wake-up ping exists to find
    n = con.execute("SELECT COUNT(*) FROM heartbeat").fetchone()[0]
    assert n == 2
    con.close()


# --- requirement 3: retention never eats evidence --------------------------

def _mkdb(tmp_path):
    db = str(tmp_path / "runs.db")
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE run_history (id INTEGER PRIMARY KEY, url TEXT, ts TEXT,"
                " status TEXT, kind TEXT, verdict TEXT, error_code TEXT,"
                " defacement_detected INTEGER)")
    old, new = ts(days=90), ts(days=1)
    rows = [
        ("plain_ok_old", old, "OK", None, None, None, 0),        # the ONLY pruneable row
        ("control_old", old, "OK", "control", None, None, 0),    # control: evidence
        ("failed_old", old, "CHECK_FAILED", None, None, "LLAMA_UNREACHABLE", 0),
        ("verdict_old", old, "OK", None, "SUSPECTED_DEFACEMENT", None, 1),
        ("plain_ok_new", new, "OK", None, None, None, 0),
    ]
    con.executemany("INSERT INTO run_history (url, ts, status, kind, verdict,"
                    " error_code, defacement_detected) VALUES (?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()
    return db


def test_prune_keeps_every_citable_row(tmp_path, capsys):
    db = _mkdb(tmp_path)
    rc = M.main(["prune", "--db", db, "--keep-days", "30"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "WOULD PRUNE 1" in out                     # dry-run default finds exactly one
    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM run_history").fetchone()[0] == 5  # dry: nothing gone
    M.main(["prune", "--db", db, "--keep-days", "30", "--apply"])
    urls = {r[0] for r in con.execute("SELECT url FROM run_history")}
    assert urls == {"control_old", "failed_old", "verdict_old", "plain_ok_new"}
    con.close()


# --- run-path failure taxonomy (offline half) -------------------------------

def test_analyze_one_unreachable_is_terminal_transport_error():
    status, detail, rid = M.analyze_one("http://127.0.0.1:1", "s", "http://x", 5)
    assert status == "TRANSPORT_ERROR" and rid is None and "127.0.0.1" in detail


def test_stale_subcommand_never_ran_is_stale(tmp_path):
    db = str(tmp_path / "runs.db")
    rc = M.main(["stale", "--db", db])
    assert rc == 1                                    # no rows ever: scheduler dead


def test_prune_legacy_schema_without_kind_column(tmp_path, capsys):
    """pvellm prod runs v0.2.1: run_history has NO `kind` column. Prune must
    degrade (keep-everything-citable semantics), not OperationalError."""
    db = str(tmp_path / "legacy.db")
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE run_history (id INTEGER PRIMARY KEY, url TEXT, ts TEXT,"
                " status TEXT)")                      # exactly the legacy shape
    con.executemany("INSERT INTO run_history (url, ts, status) VALUES (?,?,?)",
                    [("plain_old", ts(days=90), "OK"), ("plain_new", ts(days=1), "OK")])
    con.commit(); con.close()
    assert M.main(["prune", "--db", db, "--keep-days", "30", "--apply"]) == 0
    con = sqlite3.connect(db)
    assert {r[0] for r in con.execute("SELECT url FROM run_history")} == {"plain_new"}
    con.close()


def test_prune_null_defacement_is_pruneable():
    """NULL-safe SQL: `x IN (0, NULL)` is never true for NULL — a plain-OK row
    with defacement_detected NULL (older writers) must still be pruneable."""
    import tempfile, os
    with tempfile.TemporaryDirectory() as td:
        db = os.path.join(td, "runs.db")
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE run_history (id INTEGER PRIMARY KEY, url TEXT, ts TEXT,"
                    " status TEXT, kind TEXT, verdict TEXT, error_code TEXT,"
                    " defacement_detected INTEGER)")
        con.execute("INSERT INTO run_history (url, ts, status, defacement_detected)"
                    " VALUES ('null_old', ?, 'OK', NULL)", (ts(days=90),))
        con.commit(); con.close()
        assert M.main(["prune", "--db", db, "--keep-days", "30", "--apply"]) == 0
        con = sqlite3.connect(db)
        assert con.execute("SELECT COUNT(*) FROM run_history").fetchone()[0] == 0
        con.close()


def test_stale_require_ok_fails_on_blind_watcher(tmp_path, capsys):
    """Deadline met but newest terminal row CHECK_FAILED: FRESH to a pure
    deadline, STALE to --require-ok. This is the 400-storm case found on
    real pvellm prod during the first live cycle (JSON-vs-Form mismatch):
    a scheduler that runs and always fails is a BLIND watcher, not a dead
    one, and absence-of-deadline alone cannot see it."""
    db = str(tmp_path / "runs.db")
    con = M.connect(db)
    M.record_heartbeat(con, "s1", "CHECK_FAILED", 1.0, 1, "HTTP 400 storm")
    con.close()
    assert M.main(["stale", "--db", db, "--sites", "s1"]) == 0             # deadline ok
    assert M.main(["stale", "--db", db, "--sites", "s1", "--require-ok"]) == 1
    assert "BLIND" in capsys.readouterr().out

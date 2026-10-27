"""Plant-purity law (Sammy 2026-10-25, 'labels that exist must bite' #3):
synthetic run_history rows — QA controls AND validation plants — are
invisible to every product reader, and alarm citations verify against
the source. Absence is asserted the usual way: insert a plant, watch
nothing move."""
import json
import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace

import monitorlib


def _store(tmp_path):
    db = tmp_path / "runs.db"
    con = sqlite3.connect(str(db))
    con.executescript(
        "CREATE TABLE run_history (id INTEGER PRIMARY KEY, url TEXT, ts TEXT,"
        " status TEXT, verdict TEXT, kind TEXT, det_similarity REAL,"
        " det_changed_bands TEXT, capture_sanity TEXT, det_band_sims TEXT);"
        "CREATE TABLE heartbeat (site TEXT, ts TEXT, status TEXT,"
        " elapsed_s REAL, run_history_id INTEGER);")
    now = datetime.now(timezone.utc).isoformat()
    idx = tmp_path / "index.json"
    idx.write_text(json.dumps({"baselines": {"siteA": {"url": "http://s/"}}}))
    con.execute("INSERT INTO heartbeat VALUES ('siteA', ?, 'OK', 1, 1)", (now,))
    # live row first, plant on top of it: the newest row is SYNTHETIC
    con.execute("INSERT INTO run_history(url,ts,status,kind,det_similarity,"
                "det_changed_bands,capture_sanity,det_band_sims) VALUES"
                " ('http://s/',?,'OK','check',1.0,'[]','{}','[1.0]')", (now,))
    con.execute("INSERT INTO run_history(url,ts,status,kind,verdict,det_similarity,"
                "det_changed_bands,capture_sanity,det_band_sims) VALUES"
                " ('http://s/',?,'OK','qa-plant','DEFACED',0.111,'[0]','{}','[0.1]')", (now,))
    con.commit()
    return con, idx, db


def _status_args(tmp_path, db, idx, **kw):
    return SimpleNamespace(db=str(db), index=str(idx), cadence=360, grace=90,
                           verify=kw.get("verify"))


def test_status_invisible_to_plant(tmp_path, capsys):
    con, idx, db = _store(tmp_path)
    con.close()
    rc = monitorlib.cmd_status(_status_args(tmp_path, db, idx))
    out = capsys.readouterr().out
    assert "CLEAN" in out and "DEFACEMENT-RISK" not in out
    assert "low(n<3)" in out          # conf count = 1 live row, not 2
    assert rc == 0


def test_classify_never_pages_on_plant(tmp_path, monkeypatch):
    monkeypatch.setenv("MPW_PAGE_STATE_DIR", str(tmp_path))
    con, _idx, _db = _store(tmp_path)
    calls = []
    n = monitorlib.classify_and_alert(con, "siteA", {"url": "http://s/"},
                                      subprocess_run=lambda a, check=False: (calls.append(a), type("R", (), {"returncode": 0})())[1])
    assert n == 0 and calls == []      # newest DEFACED is a plant: invisible


def test_control_still_excluded_regression(tmp_path, monkeypatch):
    monkeypatch.setenv("MPW_PAGE_STATE_DIR", str(tmp_path))
    con, _idx, _db = _store(tmp_path)
    con.execute("UPDATE run_history SET kind='control' WHERE id=2")
    con.commit()
    calls = []
    assert monitorlib.classify_and_alert(
        con, "siteA", {"url": "http://s/"},
        subprocess_run=lambda a, check=False: (calls.append(a), type("R", (), {"returncode": 0})())[1]) == 0


def test_verify_rc_law(tmp_path, capsys):
    con, idx, db = _store(tmp_path)
    con.execute("INSERT INTO run_history(url,ts,status,kind) VALUES"
                " ('http://s/','t','OK','check')")   # id 3, real, no verdict
    con.commit()
    con.close()
    assert monitorlib._verify_alarm(sqlite3.connect(str(db)), 1) == 0   # real
    out = capsys.readouterr().out
    assert "REAL" in out
    assert monitorlib._verify_alarm(sqlite3.connect(str(db)), 2) == 2   # plant
    assert "SYNTHETIC" in capsys.readouterr().out
    assert monitorlib._verify_alarm(sqlite3.connect(str(db)), 999) == 3  # absent
    assert "does not" in capsys.readouterr().out

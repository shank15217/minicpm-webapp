"""B.0 acceptance battery, tree-level (Quinn's gate then her live pages):
day-one pageable = {DEFACED}; everything else asserts ABSENCE of a page
— absence is a first-class assertion, per contract."""
import sqlite3
from types import SimpleNamespace

import monitorlib


def _db(tmp_path):
    db = tmp_path / "runs.db"
    con = monitorlib.connect(str(db))
    con.execute("CREATE TABLE run_history (id INTEGER PRIMARY KEY, url TEXT,"
                " verdict TEXT, det_similarity REAL)")
    con.commit()
    return con


def _rows(con, url, verdict, sim=0.4):
    con.execute("INSERT INTO run_history(url,verdict,det_similarity) VALUES (?,?,?)",
                (url, verdict, sim))
    con.commit()


def _calls():
    calls = []
    return calls, lambda a, check=False: calls.append(a)


def test_defaced_pages_naming_site(tmp_path, monkeypatch):
    monkeypatch.setenv("MPW_PAGE_STATE_DIR", str(tmp_path))
    con = _db(tmp_path)
    _rows(con, "http://s/", "DEFACED")
    calls, run = _calls()
    n = monitorlib.classify_and_alert(con, "siteA", {"url": "http://s/"},
                                      subprocess_run=run)
    assert n == 1
    assert calls == [["systemctl", "start",
                      "mpw-page@minicpm-defaced-siteA.service"]]
    msg = (tmp_path / "msg-minicpm-defaced-siteA.txt").read_text()
    assert "http://s/" in msg and "verdict=DEFACED" in msg and "sim=0.4" in msg


def test_paused_never_pages_under_any_verdict(tmp_path, monkeypatch):
    monkeypatch.setenv("MPW_PAGE_STATE_DIR", str(tmp_path))
    con = _db(tmp_path)
    _rows(con, "http://dyn/", "DEFACED")
    calls, run = _calls()
    assert monitorlib.classify_and_alert(
        con, "dyn", {"url": "http://dyn/", "paused": "ruling"},
        subprocess_run=run) == 0
    assert calls == []                           # pause-integrity pin, pager-chain


def test_absence_changed_risk_labels_never_page(tmp_path, monkeypatch):
    monkeypatch.setenv("MPW_PAGE_STATE_DIR", str(tmp_path))
    con = _db(tmp_path)
    calls, run = _calls()
    for i, v in enumerate([None, "CHANGED", "OK", "DEFACEMENT-RISK", "DRIFT-ELEV"]):
        _rows(con, "http://s/", v)
        assert monitorlib.classify_and_alert(con, "s", {"url": "http://s/"},
                                             subprocess_run=run) == 0, v
    assert calls == []                           # §2.8: surface-and-digest only


def test_cursor_one_page_per_row_repeat_needs_new_row(tmp_path, monkeypatch):
    monkeypatch.setenv("MPW_PAGE_STATE_DIR", str(tmp_path))
    con = _db(tmp_path)
    _rows(con, "http://s/", "DEFACED")
    calls, run = _calls()
    assert monitorlib.classify_and_alert(con, "s", {"url": "http://s/"},
                                         subprocess_run=run) == 1
    assert monitorlib.classify_and_alert(con, "s", {"url": "http://s/"},
                                         subprocess_run=run) == 0   # same row
    _rows(con, "http://s/", "DEFACED")           # new event (e.g. next cycle)
    assert monitorlib.classify_and_alert(con, "s", {"url": "http://s/"},
                                         subprocess_run=run) == 1


def test_two_sites_two_instances(tmp_path, monkeypatch):
    monkeypatch.setenv("MPW_PAGE_STATE_DIR", str(tmp_path))
    con = _db(tmp_path)
    _rows(con, "http://a/", "DEFACED")
    _rows(con, "http://b/", "DEFACED")
    calls, run = _calls()
    monitorlib.classify_and_alert(con, "sA", {"url": "http://a/"}, subprocess_run=run)
    monitorlib.classify_and_alert(con, "sB", {"url": "http://b/"}, subprocess_run=run)
    insts = [c[2] for c in calls]
    assert insts == ["mpw-page@minicpm-defaced-sA.service",
                     "mpw-page@minicpm-defaced-sB.service"]   # two pages, own names

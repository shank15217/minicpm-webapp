"""Paused-roster pin (user ruling 2026-10-25: dynamic sites out of scope).
Paused index entries: no analyze call, no heartbeat, no stale-red — and
WITHOUT --index the paused site still reads stale (documents why the
stale unit must pass the index; forgetting it is loud, not silent)."""
import json
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import monitorlib


def _mk(tmp_path, paused):
    idx = {"baselines": {
        "static-one": {"url": "http://s1/", "paused": None if not paused else None},
        "dyn-two": {"url": "http://d2/"},
    }}
    if paused:
        idx["baselines"]["dyn-two"]["paused"] = "dynamic churn (user ruling)"
    ip = tmp_path / "index.json"
    ip.write_text(json.dumps(idx))
    return ip


def _db(tmp_path):
    db = tmp_path / "runs.db"
    con = monitorlib.connect(str(db))
    # cmd_run's best-effort row-id pointer touches run_history; give it
    # the minimal shape (the real one is app.py's init_run_db).
    con.execute("CREATE TABLE IF NOT EXISTS run_history (id INTEGER PRIMARY KEY, url TEXT,"
                " verdict TEXT, det_similarity REAL)")
    con.commit()
    con.close()
    return str(db)


def test_run_skips_paused(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(monitorlib, "analyze_one",
                        lambda base, site, url, to: (called.append(site) or ("OK", "x", None)))
    monkeypatch.setattr(monitorlib, "towers_health", lambda base: ("OK", "ok"))
    ip = _mk(tmp_path, paused=True)
    db = _db(tmp_path)
    rc = monitorlib.cmd_run(SimpleNamespace(index=str(ip), db=db,
                                            base_url="http://app", timeout=1))
    assert rc == 0
    assert called == ["static-one"]              # paused never analyzed
    con = monitorlib.connect(db)
    sites = [r[0] for r in con.execute("select site from heartbeat")]
    con.close()
    assert "dyn-two" not in sites


def test_stale_skips_paused_with_index(tmp_path):
    db = _db(tmp_path)
    con = monitorlib.connect(db)
    iso_old = datetime.fromtimestamp(time.time() - 99999,
                                     timezone.utc).isoformat(timespec="seconds")
    iso_now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    con.execute("insert into heartbeat(site,ts,status) values (?,?,?)",
                ("dyn-two", iso_old, "OK"))
    con.execute("insert into heartbeat(site,ts,status) values (?,?,?)",
                ("static-one", iso_now, "OK"))
    con.commit()
    con.close()
    ip = _mk(tmp_path, paused=True)
    rc = monitorlib.cmd_stale(SimpleNamespace(db=db, cadence=60, grace=30,
                                              sites="", require_ok=True,
                                              index=str(ip)))
    assert rc == 0                               # paused not counted


def test_stale_without_index_still_flags_paused(tmp_path):
    """Loud-forgetting coupling: unit must pass --index or paused reds."""
    db = _db(tmp_path)
    con = monitorlib.connect(db)
    old = __import__("datetime").datetime.fromtimestamp(
        time.time() - 99999, __import__("datetime").timezone.utc)
    con.execute("insert into heartbeat(site,ts,status) values (?,?,?)",
                ("dyn-two", old.isoformat(), "OK"))
    con.commit(); con.close()
    rc = monitorlib.cmd_stale(SimpleNamespace(db=db, cadence=60, grace=30,
                                              sites="", require_ok=True,
                                              index=""))
    assert rc == 1

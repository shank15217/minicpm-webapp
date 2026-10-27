"""Prom exporter tests (user metrics law 2026-10-27): the file a TSDB
scrapes must be schema-stable: exactly one state=1 per site, confidence
only when a delta exists, plants/paused excluded from states, cert
ledger records the FIRST sighting as a change and NOTHING until the
fingerprint actually moves."""
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import export_prom  # noqa: E402
from monitorlib import HEARTBEAT_SCHEMA, connect, ensure_run_history, record_heartbeat  # noqa: E402

FAKE = {"fingerprint": "a" * 64, "expiry": "2027-01-01T00:00:00+00:00",
        "expiry_ts": (datetime.now(timezone.utc) + timedelta(days=30)).timestamp(),
        "issuer": "Test CA", "serial": "01"}


def _store(tmp_path):
    db = str(tmp_path / "runs.db")
    con = connect(db)
    con.executescript(HEARTBEAT_SCHEMA)
    ensure_run_history(con)
    now = datetime.now(timezone.utc)
    for key in ("aaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbb", "cccccccccccccccc"):
        record_heartbeat(con, key, "OK", 1.0, None)
    con.execute("INSERT INTO run_history (ts, url, status, det_similarity)"
                " VALUES (?, 'https://a/', 'OK', 1.0)", (now.isoformat(),))
    con.execute("INSERT INTO run_history (ts, url, status, det_similarity)"
                " VALUES (?, 'https://b/', 'OK', 0.5)", (now.isoformat(),))
    con.execute("INSERT INTO run_history (ts, url, verdict, kind)"
                " VALUES (?, 'https://c/', 'DEFACED', 'check')", (now.isoformat(),))
    con.execute("INSERT INTO run_history (ts, url, verdict, kind)"
                " VALUES (?, 'https://q/', 'DEFACED', 'qa-plant')", (now.isoformat(),))
    con.commit()
    con.close()
    idx = tmp_path / "index.json"
    idx.write_text('{"baselines": {'
                   '"aaaaaaaaaaaaaaaa": {"url": "https://a/", "description": "site a"},'
                   '"bbbbbbbbbbbbbbbb": {"url": "https://b/", "description": "site b"},'
                   '"cccccccccccccccc": {"url": "https://c/", "description": "site c"},'
                   '"dddddddddddddddd": {"url": "https://q/", "description": "qa plant",'
                   ' "paused": true}}}')
    return db, str(idx)


def _export(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(export_prom, "cert_facts", lambda url, timeout=10: dict(FAKE))
    db, idx = _store(tmp_path)
    out = tmp_path / "metrics.prom"
    capsys.readouterr()
    rc = export_prom.main.__wrapped__() if hasattr(export_prom.main, "__wrapped__") else None
    # call with argv
    old = sys.argv
    sys.argv = ["export_prom", "--index", idx, "--db", db, "--out", str(out)]
    try:
        assert export_prom.main() == 0
    finally:
        sys.argv = old
    return out.read_text()


def _states(text):
    per = {}
    for ln in text.splitlines():
        if ln.startswith("mpw_site_state{"):
            site = ln.split('site="')[1].split('"')[0]
            st = ln.split('state="')[1].split('"')[0]
            v = int(ln.rsplit(" ", 1)[1])
            if v == 1:
                per.setdefault(site, []).append(st)
    return per


def test_one_state_per_site_and_labels(tmp_path, monkeypatch, capsys):
    text = _export(tmp_path, monkeypatch, capsys)
    per = _states(text)
    assert len(per) == 3, "paused qa-plant must not get state lines"
    assert all(len(v) == 1 for v in per.values()), per
    site_a = [k for k in per if k.startswith("aaaaaaaa")][0]
    assert per[site_a] == ["OK"]


def test_confidence_and_defaced_and_pending(tmp_path, monkeypatch, capsys):
    text = _export(tmp_path, monkeypatch, capsys)
    assert 'site="bbbbbbbbbbbbbbbb"' in text
    conf = [ln for ln in text.splitlines()
            if ln.startswith("mpw_site_confidence{site=\"bbbb")][0]
    assert conf.endswith(" 0.5000")
    per = _states(text)
    b = [k for k in per if k.startswith("bbbb")][0]
    c = [k for k in per if k.startswith("cccc")][0]
    assert per[b] == ["MODIFIED"], "sim below ceiling = MODIFIED"
    assert per[c] == ["DEFACED"], "eye verdict wins"


def test_cert_ledger_first_sighting_then_quiet(tmp_path, monkeypatch, capsys):
    text = _export(tmp_path, monkeypatch, capsys)
    assert 'mpw_cert_changed{site="aaaaaaaaaaaaaaaa",url="https://a/"} 1' in text
    text2 = _export(tmp_path, monkeypatch, capsys)
    assert 'mpw_cert_changed{site="aaaaaaaaaaaaaaaa",url="https://a/"} 0' in text2
    assert "mpw_cert_days_remaining" in text2 and "mpw_cert_expiry_timestamp_seconds" in text2


def test_cert_change_recorded(tmp_path, monkeypatch, capsys):
    _export(tmp_path, monkeypatch, capsys)          # ledger first cert
    monkeypatch.setattr(export_prom, "cert_facts",
                        lambda url, timeout=10: {**FAKE, "fingerprint": "b" * 64})
    db, idx = _store(tmp_path)  # fresh rows fine; cert_history persists in db
    out = tmp_path / "metrics.prom"
    import io
    from contextlib import redirect_stdout
    old = sys.argv
    sys.argv = ["export_prom", "--index", idx, "--db", db, "--out", str(out)]
    with redirect_stdout(io.StringIO()):
        export_prom.main()
    sys.argv = old
    assert 'mpw_cert_changed{site="aaaaaaaaaaaaaaaa",url="https://a/"} 1' in out.read_text()

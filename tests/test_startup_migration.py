"""S9 startup-migration pin (deploy-order trap Quinn hit live on v0.4 rollout,
QA-REPORT-6): run_history gains kind/det_similarity/det_changed_bands via ALTER
only at app startup (init_run_db via lifespan). CREATE TABLE IF NOT EXISTS
never adds columns to an existing table, so an OLD-schema runs.db must be
migrated on start and record_run must work right after without
OperationalError. Companion fact for DEPLOY.md (code owner): writing a new
schema file set under a LIVE old app hard-500s until restart; supported order
is stop -> upgrade -> start.
"""
import os
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path

OLD_SCHEMA = """
CREATE TABLE run_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT, url TEXT, baseline_key TEXT, baseline_sha256 TEXT,
    mode TEXT, backend_url TEXT, model TEXT, llama_fingerprint TEXT,
    prompt_version TEXT, status TEXT, error_code TEXT, finish_reason TEXT,
    score REAL, verdict TEXT, raw_output TEXT, page_text_sha256 TEXT,
    elapsed_s REAL
);
"""


def _make_old_db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.executescript(OLD_SCHEMA)
    con.execute("INSERT INTO run_history (ts, url, status) VALUES ('2026-01-01T00:00:00Z', 'http://x', 'OK')")
    con.commit()
    con.close()


def test_startup_migrates_old_schema_db(tmp_path):
    old_db = tmp_path / "runs.db"
    _make_old_db(old_db)
    before = {r[1] for r in sqlite3.connect(old_db).execute("PRAGMA table_info(run_history)")}
    assert "det_similarity" not in before

    code = textwrap.dedent("""
        import sqlite3
        import app
        app.init_run_db()
        app.record_run(url="http://migrated", status="OK", mode="detail")
        cols = [r[1] for r in sqlite3.connect(str(app.RUNS_DB_PATH)).execute("PRAGMA table_info(run_history)")]
        missing = {"kind", "det_similarity", "det_changed_bands"} - set(cols)
        assert not missing, f"migration missing columns: {missing}"
        rows = sqlite3.connect(str(app.RUNS_DB_PATH)).execute("SELECT count(*) FROM run_history").fetchone()[0]
        assert rows == 2, f"expected migrated row + new insert, got {rows}"
        print("MIGRATION_OK")
    """)
    env = dict(os.environ, BASELINES_DIR=str(tmp_path))
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True,
                       cwd=str(Path(__file__).resolve().parent.parent))
    assert "MIGRATION_OK" in r.stdout, f"startup migration failed: {r.stderr[-500:]}"


def test_migration_is_idempotent(tmp_path):
    old_db = tmp_path / "runs.db"
    _make_old_db(old_db)
    code = textwrap.dedent("""
        import app
        app.init_run_db()
        app.init_run_db()  # second startup on already-migrated db must not raise
        print("IDEMPOTENT_OK")
    """)
    env = dict(os.environ, BASELINES_DIR=str(tmp_path))
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True,
                       cwd=str(Path(__file__).resolve().parent.parent))
    assert "IDEMPOTENT_OK" in r.stdout, f"idempotency failed: {r.stderr[-500:]}"

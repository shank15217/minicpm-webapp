"""Drift-check fixtures (Sammy 2026-10-25, both required for acceptance):
(1) stale /etc copy (the %n-era manual-refresh class);
(2) committed-wearing-deployed — origin ahead of prod with a code-touching
    delta MUST fail, docs-only delta MUST NOT (deploy is gated).
Real throwaway git repos, no mocks of git itself.
"""
import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "drift_check.sh"

GIT_ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def _git(*a, cwd):
    subprocess.run(["git", *a], cwd=cwd, check=True, env=GIT_ENV,
                   capture_output=True)


def _mk_repo(tmp_path):
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True,
                   capture_output=True)
    prod = tmp_path / "prod"
    _git("clone", "-q", str(origin), str(prod), cwd=tmp_path)
    (prod / "deploy" / "systemd").mkdir(parents=True)
    (prod / "scripts").mkdir()
    (prod / "deploy" / "systemd" / "a.service").write_text("[Unit]\nX=1\n")
    (prod / "keep.py").write_text("x = 1\n")
    _git("add", "-A", cwd=prod)
    _git("-c", "commit.gpgsign=false", "commit", "-qm", "base", cwd=prod)
    _git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=prod)
    # second clone pushes a delta to main without prod seeing it
    other = tmp_path / "other"
    _git("clone", "-q", str(origin), str(other), cwd=tmp_path)
    _git("checkout", "-q", "-B", "main", "origin/main", cwd=other)
    return prod, other


def _run(prod, etc):
    return subprocess.run(["/bin/sh", str(SCRIPT), str(prod), str(etc)],
                          capture_output=True, text=True)


def test_clean(tmp_path):
    prod, _ = _mk_repo(tmp_path)
    etc = tmp_path / "etc"
    etc.mkdir()
    (etc / "a.service").write_text("[Unit]\nX=1\n")
    r = _run(prod, etc)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "clean" in r.stdout


def test_fixture1_stale_etc_copy(tmp_path):
    """%n-era class: /etc diverged from deploy/, nobody noticed."""
    prod, _ = _mk_repo(tmp_path)
    etc = tmp_path / "etc"
    etc.mkdir()
    (etc / "a.service").write_text("[Unit]\nX=STALE\n")
    r = _run(prod, etc)
    assert r.returncode == 1 and "differs" in r.stdout


def test_fixture1b_absent_etc_copy(tmp_path):
    prod, _ = _mk_repo(tmp_path)
    etc = tmp_path / "etc"
    etc.mkdir()
    r = _run(prod, etc)
    assert r.returncode == 1 and "ABSENT" in r.stdout


def test_fixture2_committed_not_deployed(tmp_path):
    """4803e14 class: fix committed+pushed, prod never pulled, pager
    debuted with the bug. Code-touching behind-delta must fail loud."""
    prod, other = _mk_repo(tmp_path)
    etc = tmp_path / "etc"
    etc.mkdir()
    (etc / "a.service").write_text("[Unit]\nX=1\n")
    assert _run(prod, etc).returncode == 0
    (other / "scripts").mkdir(exist_ok=True)
    (other / "scripts" / "page.sh").write_text("# fixed\n")
    _git("add", "-A", cwd=other)
    _git("-c", "commit.gpgsign=false", "commit", "-qm", "the fix", cwd=other)
    _git("push", "-q", "origin", "HEAD:main", cwd=other)
    r = _run(prod, etc)
    assert r.returncode == 1
    assert "running code" in r.stdout


def test_fixture2b_docs_only_delta_does_not_page(tmp_path):
    """Deploy is gated; a docs-only behind-delta is a note, not a page."""
    prod, other = _mk_repo(tmp_path)
    etc = tmp_path / "etc"
    etc.mkdir()
    (etc / "a.service").write_text("[Unit]\nX=1\n")
    (other / "README.md").write_text("# a doc moved\n")
    _git("add", "-A", cwd=other)
    _git("-c", "commit.gpgsign=false", "commit", "-qm", "docs", cwd=other)
    _git("push", "-q", "origin", "HEAD:main", cwd=other)
    r = _run(prod, etc)
    assert r.returncode == 0
    assert "docs-only" in r.stdout


def test_fixture3_untracked_shadow_code(tmp_path):
    """Orphan-code law (shadow ledger 2/2): an untracked .py on prod disk —
    the paired_capture/judge.py class — must fail the timer, not wear
    byte-identical luck as a status."""
    prod, _ = _mk_repo(tmp_path)
    etc = tmp_path / "etc"
    etc.mkdir()
    (etc / "a.service").write_text("[Unit]\nX=1\n")
    assert _run(prod, etc).returncode == 0          # baseline still clean
    (prod / "judge.py").write_text("# orphan bench copy\n")
    r = _run(prod, etc)
    assert r.returncode == 1
    assert "orphaned-or-modified" in r.stdout and "judge.py" in r.stdout


def test_fixture3b_modified_tracked_code(tmp_path):
    """The modified-shadow sibling: diverged working copy fails too,
    and a clean-up (revert) restores clean."""
    prod, _ = _mk_repo(tmp_path)
    etc = tmp_path / "etc"
    etc.mkdir()
    (etc / "a.service").write_text("[Unit]\nX=1\n")
    (prod / "keep.py").write_text("x = 2  # hotpatch, never committed\n")
    r = _run(prod, etc)
    assert r.returncode == 1 and "keep.py" in r.stdout
    _git("checkout", "--", "keep.py", cwd=prod)
    assert _run(prod, etc).returncode == 0

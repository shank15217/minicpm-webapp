"""Canary honest-field fixtures (Quinn 2026-10-25, filed against the live
debut digest message 6860 whose newest-sha was sha256 of empty input):
a canary that would emit sha256(''), '' or '?' as newest-sha must FAIL,
not deliver. ssh is shimmed via PATH; the POST sink is real."""
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CANARY = REPO / "scripts" / "canary.sh"
EMPTY16 = "e3b0c44298fc1c14"


class _Sink(BaseHTTPRequestHandler):
    posts = []
    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length", 0))
        _Sink.posts.append(self.rfile.read(n).decode("utf-8", "replace"))
        self.send_response(200); self.end_headers(); self.wfile.write(b"ok")
    def log_message(self, format, *a):  # noqa: A002
        pass


def _sink():
    _Sink.posts = []
    srv = HTTPServer(("127.0.0.1", 0), _Sink)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _shim(tmp_path, behaviors):
    """fake ssh: dispatch on remote command substring -> stdout"""
    bin_ = tmp_path / "bin"
    bin_.mkdir(exist_ok=True)
    body = "#!/bin/sh\ncmd=\"$*\"\n"
    for frag, (out, rc) in behaviors.items():
        body += (f'case "$cmd" in *"{frag}"*) printf "%s" \'{out}\'; '
                 f'exit {rc};;\nesac\n')
    body += "exit 0\n"
    p = bin_ / "ssh"
    p.write_text(body)
    p.chmod(0o755)
    return bin_


def _fire(env_extra, tmp_path):
    env = {"PATH": f"{tmp_path / 'bin'}:{os.defpath}:/usr/bin:/bin",
           "MPW_BACKUP_DIR": "/srv/mpwbackup"}
    env.update(env_extra)
    return subprocess.run(["/bin/sh", str(CANARY)], env=env,
                          capture_output=True, text=True)


def test_canary_shim_plumbing(tmp_path):
    """sanity: shims actually dispatch (behaviors set below)."""
    bin_ = _shim(tmp_path, {
        "wc -l": ("7", 0),
        "head -1": ("/srv/mpwbackup/baselines-z.tgz", 0),
        "sha256sum": ("aabbccddeeff0011  file", 0),
    })
    assert (bin_ / "ssh").exists()


def _run_with_shim(tmp_path, behaviors, sha_field=None):
    srv = _sink()
    bin_ = _shim(tmp_path, behaviors)
    # shim ssh swallows args; the sha behavior controls newest-sha
    r = _fire({"MPW_PAGE_URL": f"http://127.0.0.1:{srv.server_address[1]}/x",
               "MPW_BACKUP_SSH": "ssh gw"}, tmp_path)
    srv.shutdown()
    return r, list(_Sink.posts)


def test_refuses_when_sha_is_of_empty_input(tmp_path):
    r, posts = _run_with_shim(tmp_path, {
        "wc -l": ("5", 0),
        "head -1": ("/srv/mpwbackup/baselines-z.tgz", 0),
        "sha256sum": (f"{EMPTY16}deadbeef  f", 0),
    })
    assert r.returncode == 1, r.stdout
    assert "sha256-of-empty" in r.stdout
    assert posts == []          # NOTHING delivered


def test_refuses_when_sha_unreadable(tmp_path):
    r, posts = _run_with_shim(tmp_path, {
        "wc -l": ("5", 0),
        "head -1": ("/srv/mpwbackup/baselines-z.tgz", 0),
        "sha256sum": ("", 0),   # empty read through the channel
    })
    assert r.returncode == 1 and posts == []


def test_refuses_when_no_dailies(tmp_path):
    r, posts = _run_with_shim(tmp_path, {
        "wc -l": ("0", 0),
    })
    assert r.returncode == 1 and "REFUSING" in r.stdout and posts == []


def test_honest_digest_delivers(tmp_path):
    r, posts = _run_with_shim(tmp_path, {
        "wc -l": ("6", 0),
        "head -1": ("/srv/mpwbackup/baselines-z.tgz", 0),
        "sha256sum": ("c9679be612345678  f", 0),
    })
    assert r.returncode == 0, r.stdout + r.stderr
    assert len(posts) == 1
    assert "dailies=6" in posts[0] and "newest-sha=c9679be612345678" in posts[0]

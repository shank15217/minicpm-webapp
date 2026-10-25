"""Pager storm-law tests (Sammy 2026-10-25; receipt-ready for the burst
plant): a 10-failure burst yields EXACTLY two posts — full detail, then
one coalesced "N more failures since HH:MM" after the window — and the
TG branch posts form-encoded text= with the URL (token-bearing) sourced
only from the env file, never argv.
"""
import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PAGE = REPO / "scripts" / "page.sh"


class _Sink(BaseHTTPRequestHandler):
    posts = []
    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length", 0))
        _Sink.posts.append({
            "path": self.path,
            "ctype": self.headers.get("Content-Type", ""),
            "body": self.rfile.read(n).decode("utf-8", "replace"),
        })
        self.send_response(200); self.end_headers(); self.wfile.write(b"ok")
    def log_message(self, format, *a):  # noqa: A002 - match base signature
        pass


def _sink_server():
    _Sink.posts = []
    srv = HTTPServer(("127.0.0.1", 0), _Sink)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _fire(state_dir, url, style="", cooldown="3600", token=""):
    env = {"PATH": os.defpath + ":/usr/bin:/bin",
           "MPW_PAGE_URL": url, "MPW_PAGE_STATE_DIR": str(state_dir),
           "MPW_PAGE_COOLDOWN_S": cooldown}
    if style:
        env["MPW_PAGE_STYLE"] = style
    if token:
        env["MPW_PAGE_TOKEN"] = token
    return subprocess.run(["/bin/sh", str(PAGE), "minicpm-monitor-stale.service"],
                          env=env, capture_output=True, text=True)


def test_burst_ten_yields_exactly_two_posts(tmp_path):
    srv = _sink_server()
    url = f"http://127.0.0.1:{srv.server_address[1]}/topic"
    # burst of 10 inside one window
    for _ in range(10):
        r = _fire(tmp_path, url)
        assert r.returncode == 0, r.stderr
    assert len(_Sink.posts) == 1                      # only the full page
    assert "see: journalctl" in _Sink.posts[0]["body"]
    # window expiry is time-based; simulate by aging the state file
    st = next(tmp_path.glob("*.state"))
    lines = st.read_text().splitlines()
    lines[0] = str(int(lines[0]) - 3700)              # last page "an hour ago"
    st.write_text("\n".join(lines) + "\n")
    r = _fire(tmp_path, url)
    assert r.returncode == 0, r.stderr
    srv.shutdown()
    assert len(_Sink.posts) == 2                      # exactly two, ever
    assert "9 more failures since" in _Sink.posts[1]["body"]
    assert "cooldown active" in _Sink.posts[1]["body"]


def test_tg_style_posts_form_encoded_text(tmp_path):
    srv = _sink_server()
    url = f"http://127.0.0.1:{srv.server_address[1]}/botSECRET123/sendMessage"
    r = _fire(tmp_path, url, style="tg")
    srv.shutdown()
    assert r.returncode == 0, r.stderr
    p = _Sink.posts[0]
    assert p["ctype"].startswith("application/x-www-form-urlencoded")
    assert p["body"].startswith("text=mpw+ALERT") or p["body"].startswith("text=mpw%20ALERT")
    assert "botSECRET123" not in p["body"]            # token path, not body
    # token never appears in the script's own argv/stdout
    assert "botSECRET123" not in r.stdout + r.stderr


def test_unconfigured_still_fails_loud(tmp_path):
    env = {"PATH": os.defpath + ":/usr/bin:/bin",
           "MPW_PAGE_STATE_DIR": str(tmp_path)}
    r = subprocess.run(["/bin/sh", str(PAGE), "x.service"],
                       env=env, capture_output=True, text=True)
    assert r.returncode == 1 and "NOT CONFIGURED" in r.stdout


def test_corrupt_state_fails_open_posts_anyway(tmp_path):
    """Quinn's 5th contract (planted repro, verbatim shape): garbage stamp
    must NOT exit 2 before the POST — page attempted, rc in curl-land."""
    srv = _sink_server()
    url = f"http://127.0.0.1:{srv.server_address[1]}/topic"
    (tmp_path / "minicpm-monitor-stale.service.state").write_text(
        "garbage-not-a-timestamp\nalso-garbage\nnot-a-count\n")
    r = _fire(tmp_path, url)
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)  # not 2
    assert len(_Sink.posts) == 1                                  # POST happened
    # state self-healed to numeric triple
    nums = (tmp_path / "minicpm-monitor-stale.service.state"
            ).read_text().split()
    assert all(n.isdigit() for n in nums), nums
    # partial corruption (only LAST garbage) also fails open
    (tmp_path / "minicpm-monitor-stale.service.state").write_text(
        "x\n1700000000\n3\n")
    r = _fire(tmp_path, url)
    srv.shutdown()
    assert r.returncode == 0 and len(_Sink.posts) == 2

"""Gridwin windowing + towers-heartbeat (HANDOFF-10 scope items).

Gridwin (Addendum 3): window_px makes grid SHAPE deterministic for pages
taller than H, keeps a short page's own height (collapse MUST still break
shape — never launder truncation), and window_px=None is byte-identical to
the legacy call.

Towers row (Sammy hole / Quinn scope): cmd_run records a site="towers"
heartbeat from the /health gather, non-OK unless every probe is "ok";
masking fixtures pin BOTH directions of the per-site stale loop
(fresh-OK towers row must not excuse a stale site row; blind towers row
must go red while every site row is fresh) so a future "simplification"
of cmd_stale's per-site aggregation fails a test, not a review.
"""
import base64
import io
import json
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from PIL import Image

import detdiff
import monitorlib


def _png(w: int, h: int, shade: int = 128) -> bytes:
    im = Image.new("L", (w, h), shade)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


# ---------------- gridwin semantics ----------------

def test_tall_pages_converge_to_one_shape_under_window():
    g1 = detdiff.block_mean_grid(_png(1280, 4096), window_px=2560)
    g2 = detdiff.block_mean_grid(_png(1280, 5500), window_px=2560)
    assert (g1["cols"], g1["rows"]) == (g2["cols"], g2["rows"]) == (20, 40)
    assert len(base64.b64decode(g1["b64"])) == g1["cols"] * g1["rows"]


def test_short_page_keeps_own_height_no_padding():
    g = detdiff.block_mean_grid(_png(1280, 1500), window_px=2560)
    assert g["rows"] == 1500 // 64          # own height; window never pads


def test_collapse_below_window_still_breaks_shape():
    # amazon-style truncation (896px = 14 rows) must NOT be laundered
    # into the tall-page epoch: different rows == corpus break.
    g_tall = detdiff.block_mean_grid(_png(1280, 4096), window_px=2560)
    g_collapse = detdiff.block_mean_grid(_png(1280, 896), window_px=2560)
    assert g_collapse["rows"] != g_tall["rows"]


def test_window_none_is_byte_identical_to_legacy():
    b = _png(1280, 3000)
    assert (detdiff.block_mean_grid(b)
            == detdiff.block_mean_grid(b, window_px=None))


def test_boundary_crop_is_top_anchored_content_not_padding():
    # bottom-half content must not leak into the windowed grid: two pages
    # identical on top 2560px, different tails -> SAME grid.
    tail_b = Image.new("L", (1280, 4096), 200)
    px = tail_b.load()
    assert px is not None
    for y in range(2560, 4096):
        for x in range(0, 1280, 16):
            for k in range(16):
                px[min(x + k, 1279), y] = 0
    tail_a = Image.new("L", (1280, 4096), 200)   # flat-200 tail
    buf_a, buf_b = io.BytesIO(), io.BytesIO()
    tail_a.save(buf_a, format="PNG")
    tail_b.save(buf_b, format="PNG")
    ga = detdiff.block_mean_grid(buf_a.getvalue(), window_px=2560)
    gb = detdiff.block_mean_grid(buf_b.getvalue(), window_px=2560)
    assert ga["rows"] == 40                       # both windowed
    assert ga["b64"] == gb["b64"]                 # tails invisible to window
    # and WITHOUT the window the same pair differs (tail is real content):
    assert (detdiff.block_mean_grid(buf_a.getvalue())["b64"]
            != detdiff.block_mean_grid(buf_b.getvalue())["b64"])


# ---------------- towers heartbeat ----------------

class _HealthHandler(BaseHTTPRequestHandler):
    body = {}
    def do_GET(self):  # noqa: N802
        data = json.dumps(self.body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)
    def log_message(self, *a):
        pass


@pytest.fixture()
def health_server():
    srv = HTTPServer(("127.0.0.1", 0), _HealthHandler)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield srv
    srv.shutdown()


def _run_with_health(tmp_path, health_server, body):
    _HealthHandler.body = body
    idx = tmp_path / "index.json"
    idx.write_text(json.dumps({"baselines": {}}))
    db = str(tmp_path / "hb.db")
    url = f"http://127.0.0.1:{health_server.server_address[1]}"
    rc = monitorlib.main(["run", "--base-url", url,
                          "--index", str(idx), "--db", db])
    row = monitorlib.latest_heartbeat(monitorlib.connect(db), "towers")
    return rc, row


def test_towers_row_ok_when_all_probes_ok(tmp_path, health_server):
    rc, row = _run_with_health(
        tmp_path, health_server,
        {"fast": "ok", "detail": "ok", "16x": "ok", "4x": "ok"})
    assert row is not None and row[1] == "OK" and rc == 0


def test_towers_row_blind_when_one_probe_unreachable(tmp_path, health_server):
    rc, row = _run_with_health(
        tmp_path, health_server,
        {"fast": "ok", "detail": "unreachable: ConnectError",
         "16x": "ok", "4x": "ok"})
    assert row[1] == "TOWERS_BLIND" and rc != 0


def test_towers_row_unreachable_when_health_itself_dead(tmp_path):
    idx = tmp_path / "index.json"
    idx.write_text(json.dumps({"baselines": {}}))
    db = str(tmp_path / "hb.db")
    # port 1: nothing listens
    monitorlib.main(["run", "--base-url", "http://127.0.0.1:1",
                     "--index", str(idx), "--db", db])
    row = monitorlib.latest_heartbeat(monitorlib.connect(db), "towers")
    assert row[1] == "TOWERS_UNREACHABLE"     # recorded, not absent


# ---------------- masking-direction pins (Quinn 1a) ----------------

def _seed(db, rows):
    con = monitorlib.connect(db)
    for site, ts, status in rows:
        con.execute("INSERT INTO heartbeat (site, ts, status, elapsed_s,"
                    " run_history_id, detail) VALUES (?,?,?,?,?,?)",
                    (site, ts, status, 1.0, None, ""))
    con.commit()
    con.close()


def _stale_rc(tmp_path, rows):
    db = str(tmp_path / "hb.db")
    _seed(db, rows)
    return monitorlib.main(["stale", "--db", db, "--cadence", "60",
                            "--grace", "30", "--require-ok"])


def test_fresh_ok_towers_row_does_not_excuse_stale_site(tmp_path):
    now = monitorlib.utcnow().isoformat(timespec="seconds")
    old = "2026-01-01T00:00:00+00:00"
    rc = _stale_rc(tmp_path, [("towers", now, "OK"), ("siteA", old, "OK")])
    assert rc == 1                              # site staleness still red


def test_blind_towers_row_red_while_all_sites_fresh(tmp_path):
    now = monitorlib.utcnow().isoformat(timespec="seconds")
    rc = _stale_rc(tmp_path, [("siteA", now, "OK"),
                              ("siteB", now, "OK"),
                              ("towers", now, "TOWERS_BLIND")])
    assert rc == 1                              # blindness flips exit code

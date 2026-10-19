"""W10+D pins: sha256 baseline manifest + atomic index writes (2026-10-19)."""
import sys
from pathlib import Path

import pytest
from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as app_mod  # noqa: E402

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000d4944415408d763f8ffff3f0005fe02fea735b6130000000049454e44ae426082")


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    d = tmp_path / "baselines"
    d.mkdir()
    monkeypatch.setattr(app_mod, "BASELINES_DIR", d)
    monkeypatch.setattr(app_mod, "INDEX_PATH", d / "index.json")
    return d


def _png(b: bytes) -> bytes:
    return PNG  # fixed 1x1 PNG; payload irrelevant, hash covers raw bytes


def test_capture_manifests_sha(sandbox):
    key = app_mod.save_baseline("https://a.example/", PNG)
    entry = app_mod.load_index()["baselines"][key]
    import hashlib
    assert entry["sha256"] == hashlib.sha256(PNG).hexdigest()


def test_tampered_baseline_refused(sandbox):
    key = app_mod.save_baseline("https://a.example/", PNG)
    (sandbox / f"{key}.png").write_bytes(PNG + b"\x00")  # silent edit
    with pytest.raises(app_mod.CheckFailed) as e:
        app_mod.load_baseline("https://a.example/")
    assert e.value.code == "BASELINE_INTEGRITY"


def test_index_corrupt_raises_not_empty(sandbox):
    app_mod.save_baseline("https://a.example/", PNG)
    app_mod.INDEX_PATH.write_text('{"baselines": {trunca')  # half-write shape
    with pytest.raises(app_mod.CheckFailed) as e:
        app_mod.load_index()
    assert e.value.code == "INDEX_CORRUPT"  # never silently {} (bug D wipe path)


def test_no_tmp_residue(sandbox):
    app_mod.save_baseline("https://a.example/", PNG)
    assert not list(sandbox.glob("*.tmp"))


def test_integrity_report_and_health(sandbox):
    app_mod.save_baseline("https://a.example/", PNG)
    k2 = app_mod.save_baseline("https://b.example/", PNG)
    (sandbox / f"{k2}.png").write_bytes(b"garbage")
    rep = app_mod.baseline_integrity_report()
    assert rep["ok"] == 1 and rep["corrupt"] == [{"url": "https://b.example/", "why": "sha_mismatch"}]
    with TestClient(app_mod.app) as c:
        h = c.get("/health").json()
        assert h["baseline_integrity"]["corrupt"][0]["url"] == "https://b.example/"
        assert c.get("/baselines").status_code == 200
    # corrupt index: health still answers, named
    app_mod.INDEX_PATH.write_text("{broken")
    with TestClient(app_mod.app) as c:
        assert c.get("/health").status_code == 200
        h2 = c.get("/health").json()
        assert h2["baseline_integrity"]["error"] == "INDEX_CORRUPT"
        assert c.get("/baselines").json()["error_code"] == "INDEX_CORRUPT"


def test_health_boot_receipt_fields():
    """Quinn HEAD-lie pin: /health answers 'which boot am I?' from the socket."""
    import subprocess
    from pathlib import Path
    want = subprocess.run(["git", "-C", str(Path(app_mod.__file__).parent),
                           "rev-parse", "--short", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    with TestClient(app_mod.app) as c:
        h = c.get("/health").json()
        assert h["commit"] == want
        assert h["pid"] > 0 and h["started_at"].endswith("Z")


def test_blank_tail_detector_and_loader():
    """Quinn asks 1+3: sanity detector + loader procedure contract."""
    import io as _io
    from PIL import Image
    # page: 1000px content + 1300px pure-white tail (unrendered band)
    im = Image.new("RGB", (1280, 2300), (200, 60, 60))
    for y in range(1000, 2300):
        for x in range(0, 1280, 1):
            im.putpixel((x, y), (255, 255, 255))
    buf = _io.BytesIO(); im.save(buf, "PNG")
    assert app_mod.blank_tail_bands(buf.getvalue()) != []
    # fully-colored page: no blank bands (threshold must not fire on real content)
    im2 = Image.new("RGB", (1280, 2300), (200, 60, 60))
    buf2 = _io.BytesIO(); im2.save(buf2, "PNG")
    assert app_mod.blank_tail_bands(buf2.getvalue()) == []
    # loader contract: force-eager + re-measured height (no single-shot loop)
    js = app_mod.LOADER_JS
    assert "loading = 'eager'" in js
    assert "scrollHeight" in js and "pass" in js.lower()

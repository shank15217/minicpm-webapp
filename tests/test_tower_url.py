"""D9 (fresh-clone 502): the config file promises bare-or-/v1 tower URLs
are accepted EVERYWHERE; the analyze path is where that promise died.
Fixture is wildcard-shaped like the real bug: bare origin in, full chat
URL where /analyze actually POSTs."""
import subprocess
import sys


def test_canonicalizer_shapes():
    import app as m
    f = m._tower_chat_url
    assert f("http://h:8081") == "http://h:8081/v1/chat/completions"
    assert f("http://h:8081/") == "http://h:8081/v1/chat/completions"
    assert f("http://h:8081/v1") == "http://h:8081/v1/chat/completions"
    assert f("http://h:8081/v1/chat/completions") == \
        "http://h:8081/v1/chat/completions"
    assert f("") == ""


def test_import_reads_bare_env_as_full_chat_url():
    """The seam test: a clone's start_app.sh exports the BARE origin
    (reh3's exact state); the imported app's constant must already carry
    the full chat URL — verified in a fresh interpreter, not by
    re-deriving the logic in the test."""
    code = ("import app;"
            "print(app.LLAMA_SERVER_DETAIL)")
    out = subprocess.run(
        [sys.executable, "-c", code],
        env={"PATH": "/usr/bin:/bin",
             "PYTHONPATH": __import__("os").path.dirname(
                 __import__("os").path.dirname(__file__) + "/x"),
             "LLAMA_SERVER_DETAIL": "http://pvellm3.nellienet.win:8081"},
        capture_output=True, text=True, timeout=60)
    assert out.stdout.strip() == \
        "http://pvellm3.nellienet.win:8081/v1/chat/completions", out.stderr[-300:]

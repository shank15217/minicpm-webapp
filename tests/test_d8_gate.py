"""D8 (clone rehearsal): the S2 gate must know the hosts monitor.toml
names — one posture across capture stage and /analyze stage, or a fresh
box works exactly once per URL."""
import app as appmod


def test_monitor_toml_hosts_feed_the_gate():
    hosts = appmod._monitor_toml_hosts()
    assert hosts, "repo ships a monitor.toml with sites"
    pats = appmod._allowed_target_patterns()
    assert set(hosts) <= set(pats)


def test_gate_passes_monitor_only_host(tmp_path, monkeypatch):
    # simulate a clone whose monitor.toml names a host absent from the
    # built-in default list (the exact D8 failure): gate must pass it.
    monkeypatch.setattr(appmod, "_monitor_toml_hosts",
                        lambda: ["fresh-clone-site.test"])
    monkeypatch.delenv("MPW_ALLOW_TARGETS", raising=False)
    try:
        appmod.check_target("http://fresh-clone-site.test/")  # no DNS: name-pass
    except appmod.CheckFailed as e:
        assert e.code != "TARGET_NOT_ALLOWED", f"D8 still alive: {e}"


def test_gate_still_refuses_unknown_hosts(monkeypatch):
    """D8 must not widen the gate beyond the box's own config: the
    conftest-wide allow-all is removed here to exercise the real list."""
    import pytest
    monkeypatch.delenv("MPW_ALLOW_TARGETS", raising=False)
    with pytest.raises(appmod.CheckFailed) as ei:
        appmod.check_target("http://definitely-not-monitored-anywhere.test/")
    assert ei.value.code == "TARGET_NOT_ALLOWED"

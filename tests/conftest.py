import os

# Test-session default for the S2 SSRF gate: pre-gate tests use loopback
# fixtures and *.invalid probe URLs; the gate itself is exercised on its own
# terms (default-deny included) in test_ssrf_gate.py via explicit per-test
# env. Metadata-class IPs stay refused even under '*' — that rule is
# non-overridable and asserted there.
os.environ.setdefault("MPW_ALLOW_TARGETS", "*")

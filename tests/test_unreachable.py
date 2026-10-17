"""Unreachable-site classification pins (@user 2026-10-17 request).

Standard operational errors — expired cert, dead DNS, refused port, site
5xx — must arrive as distinct stable codes, never one opaque capture
failure. Offline: pure classifier over real Playwright/httpx/ssl message
strings; the live expired-cert end-to-end runs on the prod box via
scripts, not here (Playwright browsers not assumed on the dev host).
"""
import ssl
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import classify_capture_error


class _Resp:
    def __init__(self, code): self.status_code = code


class _HTTPStatus(Exception):
    def __init__(self, code):
        super().__init__(f"Server error '{code} Internal Server Error'")
        self.response = _Resp(code)


CASES = [
    # real Playwright messages (net:: codes verbatim from Chromium)
    ("Page.goto: net::ERR_CERT_DATE_INVALID at https://example.org/", "SSL_CERT_EXPIRED"),
    ("Page.goto: net::ERR_CERT_COMMON_NAME_INVALID at https://1.2.3.4/", "SSL_HOSTNAME_MISMATCH"),
    ("Page.goto: net::ERR_CERT_AUTHORITY_INVALID at https://internal.local/", "SSL_CERT_UNTRUSTED"),
    ("Page.goto: net::ERR_SSL_PROTOCOL_ERROR at https://old-tls.example/", "SSL_HANDSHAKE_FAILED"),
    ("Page.goto: net::ERR_NAME_NOT_RESOLVED at https://gone.example/", "DNS_FAILURE"),
    ("Page.goto: net::ERR_CONNECTION_REFUSED at https://dead.example/", "CONNECTION_REFUSED"),
    ("Page.goto: net::ERR_CONNECTION_RESET at https://churn.example/", "CONNECTION_RESET"),
    ("Page.goto: net::ERR_CONNECTION_TIMED_OUT at https://blackhole.example/", "TIMEOUT"),
    ("Timeout 60000ms exceeded.", "TIMEOUT"),
    ("Page.goto: net::ERR_INTERNET_DISCONNECTED at https://x.example/", "NETWORK_UNREACHABLE"),
    ("Page.goto: net::ERR_UNSAFE_PORT at http://127.0.0.1:9/", "CLIENT_UNSAFE_PORT"),
    ("connect ECONNREFUSED 127.0.0.1:12888", "CONNECTION_REFUSED"),
    # httpx / ssl paths
    ("ConnectError: [Errno -2] Name or service not known", "DNS_FAILURE"),
    ("ConnectError: [Errno 111] Connection refused", "CONNECTION_REFUSED"),
    ("ConnectTimeout: timed out", "TIMEOUT"),
]


def test_playwright_and_httpx_messages_classify():
    for msg, code in CASES:
        exc = RuntimeError(msg)
        assert classify_capture_error(exc) == code, f"{msg!r} -> {classify_capture_error(exc)} != {code}"


def test_expired_ssl_cert_exception_classifies():
    try:
        # the real thing, not a lookalike string
        ctx = ssl.create_default_context()
        raise ssl.SSLCertVerificationError(
            "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
            "certificate has expired (_ssl.c:1000)")
    except ssl.SSLCertVerificationError as e:
        assert classify_capture_error(e) == "SSL_CERT_EXPIRED"


def test_http_status_error_becomes_http_code():
    assert classify_capture_error(_HTTPStatus(503)) == "HTTP_503"


def test_unknown_failure_is_named_not_none():
    assert classify_capture_error(ValueError("quantum flux")) == "CAPTURE_FAILED"


def test_codes_are_stable_api():
    allowed = {"SSL_CERT_EXPIRED", "SSL_HOSTNAME_MISMATCH", "SSL_CERT_UNTRUSTED",
               "SSL_HANDSHAKE_FAILED", "DNS_FAILURE", "CONNECTION_REFUSED",
               "CONNECTION_RESET", "TIMEOUT", "NETWORK_UNREACHABLE", "CLIENT_UNSAFE_PORT",
               "CAPTURE_FAILED"}
    for _, code in CASES:
        assert code in allowed

#!/usr/bin/env python3
"""QA throwaway redirector for S2b per-hop SSRF tests (@qaquinn).

Serves 302 -> REDIRECT_TARGET for every path. Run on the deploy host:
    python tests/helpers/redirector.py 8012
Then: REDIRECTOR=1 REDIRECT_URL=http://127.0.0.1:8012/r pytest -k S2b
"""
import os, sys
from http.server import BaseHTTPRequestHandler, HTTPServer

TARGET = os.environ.get("REDIRECT_TARGET", "http://127.0.0.1:8080/health")

class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(302)
        self.send_header("Location", TARGET)
        self.end_headers()
    def log_message(self, *a):
        pass

if __name__ == "__main__":
    HTTPServer(("127.0.0.1", int(sys.argv[1] if len(sys.argv) > 1 else 8012)), H).serve_forever()

"""A local stand-in for the Anthropic API that records what a CLI would send (PLAN_v1.md §13 step 5).

    python swiftshader_vk/tools/wire_recorder.py --port 8765 --out runs/wire/request.json

Point a CLI at it with ANTHROPIC_BASE_URL=http://127.0.0.1:8765. The first POST body is saved
(request headers, which carry credentials, are never stored), the tool names are printed, and
every request is answered with HTTP 400 so the CLI stops without reaching the real API: no
usage is spent. Exits after the first recorded request (or --timeout seconds).
"""
from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--out", required=True)
    ap.add_argument("--timeout", type=float, default=600)
    a = ap.parse_args()
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    done = threading.Event()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *args):        # no access log: nothing about headers is kept
            pass

        def do_POST(self):
            n = int(self.headers.get("content-length") or 0)
            body = self.rfile.read(n)
            if "/messages" in self.path and not done.is_set():
                try:
                    doc = json.loads(body)
                except ValueError:
                    doc = {"unparsed": body[:2000].decode(errors="replace")}
                out.write_text(json.dumps({"path": self.path.split("?")[0], "body": doc}, indent=1))
                tools = [t.get("name") for t in doc.get("tools", [])] if isinstance(doc, dict) else []
                print(json.dumps({"recorded": str(out), "model": doc.get("model") if isinstance(doc, dict) else None,
                                  "tool_count": len(tools), "tools": tools}), flush=True)
                done.set()
            payload = json.dumps({"type": "error", "error": {"type": "invalid_request_error",
                                                              "message": "wire recorder: request recorded, not served"}}).encode()
            self.send_response(400)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            self.send_response(404)
            self.end_headers()

    srv = HTTPServer(("127.0.0.1", a.port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    done.wait(a.timeout)
    srv.shutdown()
    return 0 if done.is_set() else 1


if __name__ == "__main__":
    raise SystemExit(main())

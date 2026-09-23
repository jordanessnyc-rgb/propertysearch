"""Local web dashboard (stdlib only). `python -m dealfinder serve` then open http://127.0.0.1:8000"""
from __future__ import annotations

import json
import threading
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..analysis.underwrite import analyze_all
from ..config import Settings
from ..db import Database

INDEX = (Path(__file__).parent / "index.html").read_text(encoding="utf-8")


def _listing_json(listing) -> dict:
    return {**asdict(listing), "key": listing.key}


def serve(db: Database, settings: Settings, host: str = "127.0.0.1", port: int = 8000):
    lock = threading.Lock()   # one sqlite connection shared across request threads

    class Handler(BaseHTTPRequestHandler):
        def _send(self, body: bytes, ctype: str, status: int = 200):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, status: int = 200):
            self._send(json.dumps(obj).encode(), "application/json", status)

        def log_message(self, fmt, *args):  # keep the console quiet
            pass

        def do_GET(self):
            url = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            if url.path == "/":
                return self._send(INDEX.encode(), "text/html; charset=utf-8")
            if url.path == "/api/deals":
                with lock:
                    rows = db.top_analyses(int(q.get("limit", 500)))
                    stats = db.stats()
                deals = [{"analysis": asdict(a), "listing": _listing_json(l)} for a, l in rows]
                return self._json({"deals": deals, "stats": stats,
                                   "target_cap_rate": settings.underwriting.target_cap_rate})
            if url.path == "/api/listing":
                key = q.get("key", "")
                with lock:
                    l, a = db.get_listing(key), db.get_analysis(key)
                    history = db.price_history(key)
                if not l:
                    return self._json({"error": "not found"}, 404)
                return self._json({"listing": _listing_json(l), "analysis": asdict(a) if a else None,
                                   "price_history": history})
            self._json({"error": "not found"}, 404)

        def do_POST(self):
            if urlparse(self.path).path == "/api/analyze":
                with lock:
                    done, skipped = analyze_all(db, settings)
                return self._json({"analyzed": done, "skipped": skipped})
            self._json({"error": "not found"}, 404)

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Dashboard running at http://{host}:{port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

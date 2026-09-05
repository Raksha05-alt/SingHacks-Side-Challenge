"""Local try-out UI for investigate() - NOT part of the graded submission.

Serves a small single-page app (index.html) that lets you run the real
submissions/RakshaShalikaNethren/solution.py against the built-in incidents or
a corpus you paste in yourself, and visualizes all three pipeline
stages: retrieval ranking, correlation/hedge analysis, and the final
structured report.

Usage:
    python tryout_ui/server.py [port]   # default port 8765
"""
from __future__ import annotations

import json
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "data"))
sys.path.insert(0, str(ROOT / "submissions" / "RakshaShalikaNethren"))

from loader import load_incident  # noqa: E402
import solution  # noqa: E402

INDEX_HTML = (Path(__file__).resolve().parent / "index.html").read_text(encoding="utf-8")

BUILTIN_INCIDENTS = [
    d.name for d in sorted((ROOT / "data").iterdir())
    if d.is_dir() and (d / "query.txt").exists()
]


def run_pipeline(query: str, corpus: dict) -> dict:
    ranked = solution._retrieve_relevant_documents(query, corpus)
    evidence = solution._correlate_evidence(corpus, ranked)
    result = solution.investigate(query, corpus)

    top_ranked = [
        {"chunk_id": c.chunk_id, "file": c.file, "text": c.text, "score": round(score, 4)}
        for c, score in ranked[:15]
    ]
    evidence_payload = None
    if evidence is not None:
        evidence_payload = {
            "entity": evidence["entity"],
            "neighbors": evidence.get("neighbors", []),
            "supporting": evidence["supporting"],
            "hedged": evidence["hedged"],
            "net": evidence["net"],
        }
    return {"ranked": top_ranked, "evidence": evidence_payload, "result": result}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send_json(self, obj, status=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str):
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/":
            self._send_html(INDEX_HTML)
        elif self.path == "/api/incidents":
            self._send_json(BUILTIN_INCIDENTS)
        elif self.path.startswith("/api/incident/"):
            name = self.path[len("/api/incident/"):]
            if name not in BUILTIN_INCIDENTS:
                self._send_json({"error": f"unknown incident {name!r}"}, 404)
                return
            query, corpus = load_incident(name)
            self._send_json({"query": query, "corpus": corpus})
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path != "/api/investigate":
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
            query = data.get("query", "") or ""
            corpus = data.get("corpus", {}) or {}
            if not isinstance(corpus, dict) or not corpus:
                self._send_json({"error": "corpus must be a non-empty object of filename -> text"}, 400)
                return
            payload = run_pipeline(query, corpus)
            self._send_json(payload)
        except Exception:
            self._send_json({"error": traceback.format_exc()}, 500)


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Try-out UI serving on http://127.0.0.1:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()

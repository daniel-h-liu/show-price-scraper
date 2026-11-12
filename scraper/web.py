"""The local tracker page: pick shows, see each one's calendar of prices and price history.

    python -m scraper.web          # http://localhost:8765

Stdlib only. Checks started from the page run as `python -m scraper.run` subprocesses, so they share the
daily job's lock and never drive the browsers at the same time."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from scraper import analysis, db, emailer
from scraper.fetch import ROOT
from scraper.sites import adapter_for, check_supported

HOST, PORT = "127.0.0.1", 8765
STATIC = Path(__file__).resolve().parent / "static"
LOG = ROOT / "logs" / "web-jobs.log"

_jobs: dict[str, subprocess.Popen] = {}  # "all" or show id -> running check
_jobs_lock = threading.Lock()


def start_check(show_id: int | None) -> None:
    key = "all" if show_id is None else str(show_id)
    with _jobs_lock:
        if key in _jobs and _jobs[key].poll() is None:
            return
        LOG.parent.mkdir(parents=True, exist_ok=True)
        args = [sys.executable, "-m", "scraper.run"] + (["--no-email"] if show_id is None else ["--show-id", key])
        _jobs[key] = subprocess.Popen(args, cwd=ROOT, stdout=LOG.open("a"), stderr=subprocess.STDOUT)


def running() -> list[str]:
    with _jobs_lock:
        return [k for k, p in _jobs.items() if p.poll() is None]


def name_from_url(url: str) -> str:
    m = re.search(r"/shows/\d+-([^/?#]+)", url)
    if m:
        return re.sub(r"\s+on broadway$", "", m.group(1).replace("-", " "), flags=re.I).title()
    m = re.search(r"itemNumber=(\d+)", url)
    if m:
        return f"Met Opera #{m.group(1)}"
    m = re.search(r"/events/([^/]+)/", url)
    return m.group(1).replace("-", " ").title() if m else urlparse(url).netloc


EMAIL_RE = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[a-z]{2,}$", re.I)


def state() -> dict:
    with db.connect() as con:
        return dict(shows=[s.to_dict() for s in analysis.all_summaries(con)],
                    ended=[dict(id=r["id"], name=r["name"], url=r["url"], ended_at=r["ended_at"]) for r in db.ended_shows(con)],
                    running=running(), catalog_updated=db.catalog_updated(con),
                    owner=os.environ.get("ALERT_TO"), public_url=os.environ.get("PUBLIC_URL"),
                    signup_url=os.environ.get("SIGNUP_URL"),
                    recipients=[dict(id=r["id"], email=r["email"], name=r["name"]) for r in db.recipients(con)])


def add_recipient(body: dict) -> tuple[int, dict]:
    email = (body.get("email") or "").strip()
    if not EMAIL_RE.match(email):
        return 400, {"error": "That doesn't look like an email address."}
    with db.connect() as con:
        db.add_recipient(con, email, body.get("name"))
    return 200, {"ok": True}


def show_detail(show_id: int) -> dict | None:
    with db.connect() as con:
        sh = db.show(con, show_id)
        if not sh:
            return None
        return analysis.detail(con, sh)


def track(body: dict) -> tuple[int, dict]:
    url = (body.get("url") or "").strip()
    if not url.startswith("http"):
        return 400, {"error": "Paste a full URL starting with https://"}
    problem = check_supported(url)
    if problem:
        return 400, {"error": problem}
    with db.connect() as con:
        known = con.execute("SELECT name, image_url FROM catalog WHERE url = ?", (url,)).fetchone()
        name = (body.get("name") or "").strip() or (known["name"] if known else name_from_url(url))
        image = body.get("image_url") or (known["image_url"] if known else None)
        show_id = db.add_show(con, name, url, adapter_for(url).NAME, image)
    start_check(show_id)
    return 200, {"id": show_id, "name": name}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # keep the terminal quiet
        pass

    def _send(self, code: int, payload, ctype="application/json"):
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return {}

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            return self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
        if path == "/api/state":
            return self._send(200, state())
        if path == "/api/catalog":
            with db.connect() as con:
                return self._send(200, [dict(r) for r in db.catalog(con)])
        m = re.fullmatch(r"/api/show/(\d+)", path)
        if m:
            d = show_detail(int(m.group(1)))
            return self._send(200, d) if d else self._send(404, {"error": "no such show"})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        # only accept requests from this page (blocks other websites from posting to localhost)
        origin = self.headers.get("Origin")
        if origin and origin not in (f"http://localhost:{PORT}", f"http://{HOST}:{PORT}"):
            return self._send(403, {"error": "forbidden"})
        path, body = urlparse(self.path).path, self._body()
        if path == "/api/track":
            return self._send(*track(body))
        if path == "/api/untrack":
            with db.connect() as con:
                db.set_active(con, int(body["id"]), False)
            return self._send(200, {"ok": True})
        if path == "/api/recipients/add":
            return self._send(*add_recipient(body))
        if path == "/api/recipients/remove":
            with db.connect() as con:
                db.remove_recipient(con, int(body["id"]))
            return self._send(200, {"ok": True})
        if path == "/api/check":
            start_check(int(body["id"]) if body.get("id") else None)
            return self._send(200, {"ok": True, "running": running()})
        self._send(404, {"error": "not found"})


def main():
    emailer.load_dotenv(ROOT / ".env")
    url = f"http://localhost:{PORT}"
    try:
        server = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError:  # already running in another terminal
        print(f"show tracker already running: {url}", flush=True)
        webbrowser.open(url)
        return
    print(f"show tracker: {url}  (Ctrl-C to stop)", flush=True)
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

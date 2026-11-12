"""SQLite storage (data/tracker.db): tracked shows, every check, every performance's price per check,
and the TodayTix catalog. All times are naive New York local ISO strings."""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from scraper.models import CheckResult, now_local

DEFAULT_PATH = Path(os.environ.get("SHOW_TRACKER_DB") or Path(__file__).resolve().parent.parent / "data" / "tracker.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS shows (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  url TEXT NOT NULL UNIQUE,
  site TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1,
  image_url TEXT,
  added_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS checks (
  id INTEGER PRIMARY KEY,
  show_id INTEGER NOT NULL REFERENCES shows(id),
  checked_at TEXT NOT NULL,
  status TEXT NOT NULL,
  reason TEXT,
  seen INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS checks_show ON checks(show_id, checked_at);
CREATE TABLE IF NOT EXISTS prices (
  check_id INTEGER NOT NULL REFERENCES checks(id),
  show_id INTEGER NOT NULL REFERENCES shows(id),
  performance TEXT NOT NULL,
  price_cents INTEGER NOT NULL,
  detail TEXT
);
CREATE INDEX IF NOT EXISTS prices_perf ON prices(show_id, performance);
CREATE INDEX IF NOT EXISTS prices_check ON prices(check_id);
CREATE TABLE IF NOT EXISTS catalog (
  url TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  category TEXT NOT NULL,
  image_url TEXT,
  from_price_cents INTEGER,
  venue TEXT,
  updated_at TEXT NOT NULL
);
"""


@contextmanager
def connect(path: Path = DEFAULT_PATH):
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(SCHEMA)
    try:
        yield con
        con.commit()
    finally:
        con.close()


# ---- shows ----

def add_show(con, name: str, url: str, site: str, image_url: str | None = None) -> int:
    """Track a show (re-activating it if it was tracked before). Returns its id."""
    row = con.execute("SELECT id FROM shows WHERE url = ?", (url,)).fetchone()
    if row:
        con.execute("UPDATE shows SET active = 1, name = ?, image_url = COALESCE(?, image_url) WHERE id = ?",
                    (name, image_url, row["id"]))
        return row["id"]
    cur = con.execute("INSERT INTO shows (name, url, site, image_url, added_at) VALUES (?, ?, ?, ?, ?)",
                      (name, url, site, image_url, now_local()))
    return cur.lastrowid


def set_active(con, show_id: int, active: bool) -> None:
    con.execute("UPDATE shows SET active = ? WHERE id = ?", (int(active), show_id))


def shows(con, active_only: bool = True) -> list[sqlite3.Row]:
    q = "SELECT * FROM shows" + (" WHERE active = 1" if active_only else "") + " ORDER BY name COLLATE NOCASE"
    return con.execute(q).fetchall()


def show(con, show_id: int) -> sqlite3.Row | None:
    return con.execute("SELECT * FROM shows WHERE id = ?", (show_id,)).fetchone()


# ---- checks ----

def record_check(con, show_id: int, r: CheckResult) -> int:
    cur = con.execute("INSERT INTO checks (show_id, checked_at, status, reason, seen) VALUES (?, ?, ?, ?, ?)",
                      (show_id, r.checked_at, r.status.value, r.reason, r.seen))
    con.executemany("INSERT INTO prices (check_id, show_id, performance, price_cents, detail) VALUES (?, ?, ?, ?, ?)",
                    [(cur.lastrowid, show_id, o.performance, o.price_cents, o.detail) for o in r.performances])
    return cur.lastrowid


def latest_check(con, show_id: int) -> sqlite3.Row | None:
    return con.execute("SELECT * FROM checks WHERE show_id = ? ORDER BY checked_at DESC, id DESC LIMIT 1",
                       (show_id,)).fetchone()


def latest_good_check(con, show_id: int) -> sqlite3.Row | None:
    return con.execute("SELECT * FROM checks WHERE show_id = ? AND status != 'scrape_failed' "
                       "ORDER BY checked_at DESC, id DESC LIMIT 1", (show_id,)).fetchone()


def check_prices(con, check_id: int) -> list[sqlite3.Row]:
    return con.execute("SELECT performance, price_cents, detail FROM prices WHERE check_id = ? ORDER BY performance",
                       (check_id,)).fetchall()


def daily_prices(con, show_id: int, before: str | None = None) -> dict[str, list[tuple[str, int]]]:
    """performance -> [(day, price)] using the LAST check of each day, oldest first.
    `before` (a checked_at) excludes that check and anything later."""
    q = """
      SELECT p.performance, substr(c.checked_at, 1, 10) AS day, p.price_cents, c.checked_at
      FROM prices p JOIN checks c ON c.id = p.check_id
      WHERE p.show_id = ?""" + (" AND c.checked_at < ?" if before else "") + """
      ORDER BY p.performance, c.checked_at"""
    args = (show_id, before) if before else (show_id,)
    out: dict[str, dict[str, int]] = {}
    for row in con.execute(q, args):
        out.setdefault(row["performance"], {})[row["day"]] = row["price_cents"]  # later check same day wins
    return {perf: sorted(days.items()) for perf, days in out.items()}


def cheapest_over_time(con, show_id: int) -> list[tuple[str, int]]:
    """[(day, cheapest upcoming price that day)] -- last check per day, only performances after the check."""
    q = """
      SELECT substr(c.checked_at, 1, 10) AS day, c.checked_at, MIN(p.price_cents) AS low
      FROM checks c JOIN prices p ON p.check_id = c.id
      WHERE c.show_id = ? AND p.performance > c.checked_at
      GROUP BY c.id ORDER BY c.checked_at"""
    out: dict[str, int] = {}
    for row in con.execute(q, (show_id,)):
        out[row["day"]] = row["low"]
    return sorted(out.items())


# ---- catalog ----

def replace_catalog(con, rows: list[dict]) -> None:
    con.execute("DELETE FROM catalog")
    con.executemany("INSERT OR REPLACE INTO catalog (url, name, category, image_url, from_price_cents, venue, updated_at) "
                    "VALUES (:url, :name, :category, :image_url, :from_price_cents, :venue, :updated_at)", rows)


def catalog(con) -> list[sqlite3.Row]:
    return con.execute("""
      SELECT c.*, s.id AS show_id, COALESCE(s.active, 0) AS tracked
      FROM catalog c LEFT JOIN shows s ON s.url = c.url
      ORDER BY c.category, c.name COLLATE NOCASE""").fetchall()


def catalog_updated(con) -> str | None:
    row = con.execute("SELECT MAX(updated_at) AS t FROM catalog").fetchone()
    return row["t"] if row else None

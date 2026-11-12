"""SQLite storage (data/tracker.db): tracked shows, every check, every performance's price per check,
and the TodayTix catalog. All times are naive New York local ISO strings."""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
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
CREATE TABLE IF NOT EXISTS recipients (
  id INTEGER PRIMARY KEY,
  email TEXT NOT NULL UNIQUE COLLATE NOCASE,
  name TEXT,
  added_at TEXT NOT NULL
);
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
    _migrate(con)
    try:
        yield con
        con.commit()
    finally:
        con.close()


def _migrate(con) -> None:
    cols = {r["name"] for r in con.execute("PRAGMA table_info(shows)")}
    if "ended_at" not in cols:  # set when a show has no upcoming performances left; it stops being checked
        con.execute("ALTER TABLE shows ADD COLUMN ended_at TEXT")


# ---- shows ----

def add_show(con, name: str, url: str, site: str, image_url: str | None = None) -> int:
    """Track a show (re-activating it if it was tracked before). Returns its id."""
    row = con.execute("SELECT id FROM shows WHERE url = ?", (url,)).fetchone()
    if row:
        con.execute("UPDATE shows SET active = 1, ended_at = NULL, name = ?, image_url = COALESCE(?, image_url) "
                    "WHERE id = ?",
                    (name, image_url, row["id"]))
        return row["id"]
    cur = con.execute("INSERT INTO shows (name, url, site, image_url, added_at) VALUES (?, ?, ?, ?, ?)",
                      (name, url, site, image_url, now_local()))
    return cur.lastrowid


def set_active(con, show_id: int, active: bool) -> None:
    con.execute("UPDATE shows SET active = ? WHERE id = ?", (int(active), show_id))


def shows(con, active_only: bool = True) -> list[sqlite3.Row]:
    """Tracked shows that are still running (active_only), or every show ever added."""
    q = "SELECT * FROM shows" + (" WHERE active = 1 AND ended_at IS NULL" if active_only else "") + \
        " ORDER BY name COLLATE NOCASE"
    return con.execute(q).fetchall()


def ended_shows(con) -> list[sqlite3.Row]:
    return con.execute("SELECT * FROM shows WHERE active = 1 AND ended_at IS NOT NULL "
                       "ORDER BY ended_at DESC").fetchall()


ENDED_GRACE_DAYS = 3  # if checks are failing, wait this long after the last known performance


def end_if_finished(con, show_id: int, now: str | None = None) -> bool:
    """Mark a show ended when every performance it ever listed is in the past and either the latest
    check found nothing upcoming or the last performance was over ENDED_GRACE_DAYS ago. A show that
    never listed a performance (e.g. not on sale yet) is never ended. Returns True if it was ended."""
    now = now or now_local()
    last_perf = con.execute("SELECT MAX(performance) AS m FROM prices WHERE show_id = ?", (show_id,)).fetchone()["m"]
    if last_perf is None or last_perf > now:
        return False
    last = latest_check(con, show_id)
    grace_over = (datetime.fromisoformat(now) - datetime.fromisoformat(last_perf)).days >= ENDED_GRACE_DAYS
    if (last and last["status"] != "scrape_failed") or grace_over:
        con.execute("UPDATE shows SET ended_at = ? WHERE id = ? AND ended_at IS NULL", (now, show_id))
        return True
    return False


def fail_streak_days(con, show_id: int) -> int:
    """How many most-recent days in a row this show's last check of the day failed."""
    days: dict[str, str] = {}
    for r in con.execute("SELECT substr(checked_at, 1, 10) AS day, status FROM checks WHERE show_id = ? "
                         "ORDER BY checked_at, id", (show_id,)):
        days[r["day"]] = r["status"]
    n = 0
    for day in sorted(days, reverse=True):
        if days[day] != "scrape_failed":
            break
        n += 1
    return n


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


# ---- digest recipients (besides ALERT_TO) ----

def recipients(con) -> list[sqlite3.Row]:
    return con.execute("SELECT * FROM recipients ORDER BY added_at").fetchall()


def add_recipient(con, email: str, name: str | None = None) -> int:
    con.execute("INSERT INTO recipients (email, name, added_at) VALUES (?, ?, ?) "
                "ON CONFLICT(email) DO UPDATE SET name = COALESCE(excluded.name, name)",
                (email.strip(), (name or "").strip() or None, now_local()))
    return con.execute("SELECT id FROM recipients WHERE email = ?", (email.strip(),)).fetchone()["id"]


def remove_recipient(con, recipient_id: int) -> None:
    con.execute("DELETE FROM recipients WHERE id = ?", (recipient_id,))


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

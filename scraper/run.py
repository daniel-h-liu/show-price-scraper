"""The daily job, and manual checks.

    python -m scraper.run                  # refresh catalog, check every tracked show, email the digest
    python -m scraper.run --show-id 3      # check one show now (no catalog refresh, no email)
    python -m scraper.run --no-email       # full check, skip the email
    python -m scraper.run --digest-only    # email a digest from what's already stored
    python -m scraper.run --dry-run        # check and print; store nothing, send nothing
    python -m scraper.run --test-email     # verify Gmail settings

Only one run touches the browsers at a time (data/run.lock); a second run waits for the first.
"""
from __future__ import annotations

import argparse
import fcntl
import sys
import time
from contextlib import contextmanager
from datetime import datetime

from scraper import analysis, catalog, db, digest, emailer
from scraper.fetch import ROOT, browsers, fetch
from scraper.models import CheckResult, Status, now_local
from scraper.sites import adapter_for

LOCK = ROOT / "data" / "run.lock"
PAUSE_BETWEEN_SHOWS_S = 3


def log(msg: str) -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


@contextmanager
def run_lock():
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with LOCK.open("w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def describe(r: CheckResult) -> str:
    if r.status is Status.AVAILABLE:
        c = r.cheapest
        return (f"{len(r.performances)} performances, cheapest {digest.money(c.price_cents)} on {c.performance}"
                + (f" ({c.detail})" if c.detail else "") + (f" [{r.reason}]" if r.reason else ""))
    return r.status.value + (f": {r.reason}" if r.reason else "")


def check_shows(con, shows, dry_run: bool) -> None:
    # headless sites first, then real Chrome, so the Chrome window opens once at the end
    shows = sorted(shows, key=lambda s: getattr(adapter_for(s["url"]), "BROWSER", "") == "chrome")
    with browsers() as b:
        for i, s in enumerate(shows):
            if i:
                time.sleep(PAUSE_BETWEEN_SHOWS_S)
            r = fetch(b, s["url"])
            log(f"{s['name']}: {describe(r)}")
            if not dry_run:
                db.record_check(con, s["id"], r)
                con.commit()


def send_digest(con) -> int:
    summaries = analysis.all_summaries(con)
    if not summaries:
        log("no tracked shows; no digest sent")
        return 0
    try:
        cfg = emailer.EmailConfig.from_env()
    except ValueError as ex:
        log(f"digest not sent: {ex}")
        return 1
    err = emailer.send(cfg, digest.subject(summaries, now_local()), digest.text(summaries), digest.html(summaries))
    log("digest sent" if err is None else f"digest FAILED: {err}")
    return 0 if err is None else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--show-id", type=int, action="append")
    ap.add_argument("--no-email", action="store_true")
    ap.add_argument("--digest-only", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--test-email", action="store_true")
    args = ap.parse_args(argv)
    emailer.load_dotenv(ROOT / ".env")

    if args.test_email:
        err = emailer.send(emailer.EmailConfig.from_env(), "show-price-scraper test", "Email is working.")
        log("test email sent" if err is None else f"test email FAILED: {err}")
        return 0 if err is None else 1

    with db.connect() as con:
        if args.digest_only:
            return send_digest(con)
        with run_lock():
            if args.show_id:
                targets = [s for s in (db.show(con, i) for i in args.show_id) if s]
            else:
                if not args.dry_run:
                    try:
                        log("catalog: " + catalog.refresh(con))
                    except Exception as e:  # the picker keeps yesterday's catalog
                        log(f"catalog refresh failed: {type(e).__name__}: {e}")
                targets = db.shows(con)
            log(f"checking {len(targets)} show(s)")
            check_shows(con, targets, args.dry_run)
        if args.show_id or args.no_email or args.dry_run:
            return 0
        return send_digest(con)


if __name__ == "__main__":
    sys.exit(main())

"""The daily job, and manual checks.

    python -m scraper.run                  # refresh catalog, check every tracked show, email the digest
    python -m scraper.run --show-id 3      # check one show now (no catalog refresh, no email)
    python -m scraper.run --no-email       # full check, skip the email
    python -m scraper.run --digest-only    # email the owner a digest from what's already stored
    python -m scraper.run --dry-run        # check and print; store nothing, send nothing
    python -m scraper.run --test-email     # verify Gmail settings
    python -m scraper.run --friend-preview # email yourself the version friends get

A full run also emails the friends' copy to everyone added on the page, and publishes the read-only site
when PUBLIC_URL is set. If it crashes, the owner gets a failure email instead of a digest.

Only one run touches the browsers at a time (data/run.lock); a second run waits for the first.
"""
from __future__ import annotations

import argparse
import fcntl
import os
import sys
import time
import traceback
from contextlib import contextmanager
from datetime import datetime

from scraper import analysis, catalog, db, digest, emailer, publish
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
                if db.end_if_finished(con, s["id"]):
                    log(f"{s['name']}: no upcoming performances; stopped checking it")
                con.commit()


def send_digest(con, include_friends: bool = True) -> int:
    """The owner's digest to ALERT_TO, plus the friends' copy to everyone in recipients."""
    summaries = analysis.all_summaries(con)
    if not summaries:
        log("no tracked shows; no digest sent")
        return 0
    try:
        cfg = emailer.EmailConfig.from_env()
    except ValueError as ex:
        log(f"digest not sent: {ex}")
        return 1
    today = now_local()
    ended = [dict(r) for r in db.ended_shows(con) if r["ended_at"][:10] == today[:10]]
    subj = digest.subject(summaries, today)
    msgs = [(cfg.to, subj, digest.text(summaries, ended=ended), digest.html(summaries, ended=ended))]
    if include_friends:
        a = digest.friend_audience(os.environ.get("PUBLIC_URL"))
        ftext, fhtml = digest.text(summaries, a), digest.html(summaries, a)
        msgs += [(r["email"], subj, ftext, fhtml) for r in db.recipients(con) if r["email"].lower() != cfg.to.lower()]
    errs = emailer.send_all(cfg, msgs)
    failed = [f"{m[0] if i else 'owner'}: {e}" for i, (m, e) in enumerate(zip(msgs, errs)) if e]
    log(f"digest sent to {len(msgs) - len(failed)} of {len(msgs)}" + (f"; FAILED {'; '.join(failed)}" if failed else ""))
    return 1 if errs[0] else 0


def publish_site(con) -> None:
    if not os.environ.get("PUBLIC_URL"):
        return
    try:
        log(publish.publish(con))
    except Exception as e:  # the email already went out; the site just stays a day behind
        log(f"publish FAILED: {type(e).__name__}: {e}")


def report_crash(exc: BaseException) -> None:
    """The run itself blew up: tell the owner instead of silently sending nothing."""
    tb = "".join(traceback.format_exception(exc))[-3000:]
    try:
        err = emailer.send(emailer.EmailConfig.from_env(), "Show tracker FAILED this morning",
                           f"The daily check crashed, so there's no digest today.\n\nLog: {ROOT / 'logs' / 'run.log'}\n\n{tb}")
        log("crash report sent" if err is None else f"crash report FAILED: {err}")
    except ValueError as e:
        log(f"crash report not sent: {e}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--show-id", type=int, action="append")
    ap.add_argument("--no-email", action="store_true")
    ap.add_argument("--digest-only", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--test-email", action="store_true")
    ap.add_argument("--friend-preview", action="store_true", help="email yourself the friends' version")
    args = ap.parse_args(argv)
    emailer.load_dotenv(ROOT / ".env")

    if args.friend_preview:
        with db.connect() as con:
            sums = analysis.all_summaries(con)
            a = digest.friend_audience(os.environ.get("PUBLIC_URL"))
            err = emailer.send(emailer.EmailConfig.from_env(), "[friend preview] " + digest.subject(sums, now_local()),
                               digest.text(sums, a), digest.html(sums, a))
        log("friend preview sent" if err is None else f"friend preview FAILED: {err}")
        return 0 if err is None else 1

    if args.test_email:
        err = emailer.send(emailer.EmailConfig.from_env(), "show-price-scraper test", "Email is working.")
        log("test email sent" if err is None else f"test email FAILED: {err}")
        return 0 if err is None else 1

    full_run = not (args.show_id or args.no_email or args.dry_run or args.digest_only)
    try:
        return _run(args)
    except Exception as e:
        log(f"run crashed: {type(e).__name__}: {e}")
        if full_run:
            report_crash(e)
        raise


def _run(args) -> int:
    with db.connect() as con:
        if args.digest_only:
            return send_digest(con, include_friends=False)
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
        code = send_digest(con)
        publish_site(con)
        return code


if __name__ == "__main__":
    sys.exit(main())

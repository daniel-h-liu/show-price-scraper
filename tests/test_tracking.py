"""Check contract, storage, usual-price / deal analysis, digest, web helpers."""
import pytest

from scraper import analysis, db, digest, emailer
from scraper.models import CheckResult, Offer, Status, failed

URL = "https://www.todaytix.com/nyc/shows/47264-a-few-good-men"


def check(at, prices, status="available"):
    perfs = [Offer(c, p) for p, c in prices.items()]
    return CheckResult(status, perfs, checked_at=at)


@pytest.fixture
def con(tmp_path, monkeypatch):
    monkeypatch.setattr(analysis, "now_local", lambda: "2026-11-10T00:00")
    with db.connect(tmp_path / "t.db") as c:
        yield c


# ---- the hard rule: only available carries prices ----

def test_only_available_carries_prices():
    with pytest.raises(ValueError):
        CheckResult(Status.SOLD_OUT, [Offer(100, "2026-12-01T20:00")])
    with pytest.raises(ValueError):
        CheckResult(Status.AVAILABLE, [])
    with pytest.raises(ValueError):
        CheckResult(Status.SCRAPE_FAILED)  # needs a reason
    assert failed("blocked").performances == ()


# ---- storage + analysis ----

def test_usual_price_and_deal(con):
    sid = db.add_show(con, "A Few Good Men", URL, "todaytix")
    d1, d2 = "2026-12-23T20:00", "2026-12-24T20:00"
    db.record_check(con, sid, check("2026-11-07T08:00", {d1: 10000, d2: 9000}))
    db.record_check(con, sid, check("2026-11-08T08:00", {d1: 11000, d2: 9000}))
    db.record_check(con, sid, check("2026-11-08T20:00", {d1: 10000, d2: 9000}))  # same day: later wins
    db.record_check(con, sid, check("2026-11-09T08:00", {d1: 5000, d2: 8800}))
    s = analysis.summarize(con, db.show(con, sid))
    p1, p2 = s.perfs
    assert (p1.usual_cents, p1.history_days, p1.previous_cents) == (10000, 2, 10000)
    assert p1.deal and p1.change_cents == -5000
    assert not p2.deal and p2.change_cents == -200
    assert s.cheapest[0].performance == d1
    assert s.trend == [("2026-11-07", 9000), ("2026-11-08", 9000), ("2026-11-09", 5000)]
    assert s.low_change_cents == -4000


def test_no_deal_without_enough_history(con):
    sid = db.add_show(con, "S", URL, "todaytix")
    db.record_check(con, sid, check("2026-11-08T08:00", {"2026-12-01T20:00": 10000}))
    db.record_check(con, sid, check("2026-11-09T08:00", {"2026-12-01T20:00": 1000}))
    assert not analysis.summarize(con, db.show(con, sid)).perfs[0].deal


def test_failed_check_keeps_last_good_prices(con):
    sid = db.add_show(con, "S", URL, "todaytix")
    db.record_check(con, sid, check("2026-11-08T08:00", {"2026-12-01T20:00": 7000}))
    db.record_check(con, sid, CheckResult(Status.SCRAPE_FAILED, reason="blocked: HTTP 403", checked_at="2026-11-09T08:00"))
    s = analysis.summarize(con, db.show(con, sid))
    assert s.status == "scrape_failed" and s.last_good_at == "2026-11-08T08:00"
    assert s.perfs[0].price_cents == 7000


def test_past_performances_hidden(con, monkeypatch):
    monkeypatch.setattr(analysis, "now_local", lambda: "2026-12-01T21:00")
    sid = db.add_show(con, "S", URL, "todaytix")
    db.record_check(con, sid, check("2026-11-30T08:00", {"2026-12-01T20:00": 7000, "2026-12-02T20:00": 8000}))
    assert [p.performance for p in analysis.summarize(con, db.show(con, sid)).perfs] == ["2026-12-02T20:00"]


def test_track_untrack_retrack(con):
    sid = db.add_show(con, "S", URL, "todaytix")
    db.set_active(con, sid, False)
    assert db.shows(con) == []
    assert db.add_show(con, "S2", URL, "todaytix") == sid
    assert db.shows(con)[0]["name"] == "S2"


# ---- digest ----

def test_digest(con):
    sid = db.add_show(con, "A Few Good Men", URL, "todaytix")
    for day, price in (("07", 10000), ("08", 10000), ("09", 5000)):
        db.record_check(con, sid, check(f"2026-11-{day}T08:00", {"2026-12-23T20:00": price, "2026-12-26T14:00": 12000}))
    sid2 = db.add_show(con, "Samson", "https://www.metopera.org/smartseat?itemNumber=1", "metopera")
    db.record_check(con, sid2, CheckResult(Status.SCRAPE_FAILED, reason="blocked: HTTP 403", checked_at="2026-11-09T08:00"))
    sums = analysis.all_summaries(con)
    subj = digest.subject(sums, "2026-11-09T08:00")
    assert subj == "Show prices Mon Nov 9 — 1 deal: A Few Good Men $50"
    txt = digest.text(sums)
    assert "Wed Dec 23, 8 PM" in txt and "(usually $100)" in txt and "DEAL: 50% below usual" in txt
    assert "Sat Dec 26, 2 PM" in txt
    assert "Couldn't check today: blocked: HTTP 403" in txt
    html = digest.html(sums)
    assert "#show-" in html and "DEAL" in html


class FakeSMTP:
    sent = []

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self):
        pass

    def login(self, u, p):
        if p == "bad":
            raise RuntimeError("auth failed")

    def send_message(self, m):
        FakeSMTP.sent.append(m)


def test_send_html_email():
    cfg = emailer.EmailConfig("me@x", "pw", "me@x")
    assert emailer.send(cfg, "s", "plain", "<b>html</b>", smtp_factory=FakeSMTP) is None
    m = FakeSMTP.sent[-1]
    assert m.is_multipart() and m.get_body(("html",)).get_content().strip() == "<b>html</b>"
    assert "auth failed" in emailer.send(emailer.EmailConfig("a", "bad", "b"), "s", "b", smtp_factory=FakeSMTP)


# ---- web helpers ----

def test_name_from_url():
    from scraper.web import name_from_url
    assert name_from_url("https://www.todaytix.com/nyc/shows/46289-galileo-on-broadway") == "Galileo"
    assert name_from_url("https://www.metopera.org/smartseat?itemNumber=19152#/") == "Met Opera #19152"
    assert name_from_url("https://us.atgtickets.com/events/hadestown/walter-kerr-theatre/calendar/") == "Hadestown"


# ---- failure streak, auto-stop, recipients, friends' digest ----

def test_fail_streak_counts_recent_failed_days(con):
    sid = db.add_show(con, "S", URL, "todaytix")
    db.record_check(con, sid, check("2026-11-07T08:00", {"2026-12-01T20:00": 100}))
    for day in ("08", "09"):
        db.record_check(con, sid, CheckResult(Status.SCRAPE_FAILED, reason="blocked", checked_at=f"2026-11-{day}T08:00"))
    assert db.fail_streak_days(con, sid) == 2
    db.record_check(con, sid, check("2026-11-09T12:00", {"2026-12-01T20:00": 100}))  # later same day succeeded
    assert db.fail_streak_days(con, sid) == 0


def test_show_ends_when_every_performance_is_past(con):
    sid = db.add_show(con, "Opera", URL, "metopera")
    db.record_check(con, sid, check("2026-11-20T08:00", {"2026-11-27T19:30": 2500}))
    assert not db.end_if_finished(con, sid, now="2026-11-26T08:00")          # still upcoming
    db.record_check(con, sid, CheckResult(Status.SCRAPE_FAILED, reason="blocked", checked_at="2026-11-28T08:00"))
    assert not db.end_if_finished(con, sid, now="2026-11-28T08:00")          # failing: wait out the grace period
    assert db.end_if_finished(con, sid, now="2026-12-01T08:00")
    assert db.shows(con) == [] and db.ended_shows(con)[0]["name"] == "Opera"
    db.add_show(con, "Opera", URL, "metopera")                               # tracking again revives it
    assert db.shows(con)[0]["ended_at"] is None


def test_show_ends_right_away_when_check_finds_nothing_upcoming(con):
    sid = db.add_show(con, "S", URL, "todaytix")
    db.record_check(con, sid, check("2026-11-20T08:00", {"2026-11-27T19:30": 2500}))
    db.record_check(con, sid, CheckResult(Status.NO_INVENTORY, checked_at="2026-11-28T08:00"))
    assert db.end_if_finished(con, sid, now="2026-11-28T08:00")


def test_show_never_listed_is_not_ended(con):
    sid = db.add_show(con, "Coming soon", URL, "todaytix")
    db.record_check(con, sid, CheckResult(Status.NO_INVENTORY, checked_at="2026-11-28T08:00"))
    assert not db.end_if_finished(con, sid, now="2026-11-28T08:00")


def test_recipients(con):
    db.add_recipient(con, "Pal@Example.com", "Pal")
    db.add_recipient(con, "pal@example.com")  # same address, any case: no duplicate, name kept
    rows = db.recipients(con)
    assert len(rows) == 1 and rows[0]["name"] == "Pal"
    db.remove_recipient(con, rows[0]["id"])
    assert db.recipients(con) == []


def test_owner_vs_friend_digest(con):
    sid = db.add_show(con, "Galileo", URL, "todaytix")
    db.record_check(con, sid, check("2026-11-07T08:00", {"2026-12-01T20:00": 6200}))
    for day in ("08", "09"):
        db.record_check(con, sid, CheckResult(Status.SCRAPE_FAILED, reason="blocked: HTTP 403", checked_at=f"2026-11-{day}T08:00"))
    sums = analysis.all_summaries(con)
    ended = [{"name": "Samson et Dalila"}]
    owner = digest.text(sums, ended=ended)
    assert "NEEDS ATTENTION" in owner and "for 2 days" in owner and "Stopped checking Samson et Dalila" in owner
    assert "localhost:8765" in owner
    friend_a = digest.friend_audience("https://me.github.io/show-price-scraper/")
    friend = digest.text(sums, friend_a, ended=ended)
    assert "NEEDS ATTENTION" not in friend and "blocked" not in friend and "localhost" not in friend
    assert "Couldn't check today." in friend and "Reply to this email to stop" in friend
    html = digest.html(sums, friend_a)
    assert f'href="https://me.github.io/show-price-scraper/#show-{sid}"' in html and "localhost" not in html
    assert "localhost" not in digest.html(sums, digest.friend_audience(None))


def test_send_all_addresses_each_recipient_separately():
    FakeSMTP.sent.clear()
    cfg = emailer.EmailConfig("me@x", "pw", "me@x")
    errs = emailer.send_all(cfg, [("me@x", "s", "a", None), ("pal@y", "s", "b", "<i>b</i>")], smtp_factory=FakeSMTP)
    assert errs == [None, None]
    assert [m["To"] for m in FakeSMTP.sent] == ["me@x", "pal@y"]
    assert all(m["Cc"] is None and m["Bcc"] is None for m in FakeSMTP.sent)


def test_publish_builds_read_only_site(con, tmp_path):
    from scraper import publish
    sid = db.add_show(con, "Galileo", URL, "todaytix", "https://images.todaytix.com/poster.jpg")
    db.record_check(con, sid, check("2026-11-09T08:00", {"2026-12-01T20:00": 6200}))
    db.add_recipient(con, "secret-friend@example.com")
    out = tmp_path / "site"
    assert publish.build(con, out, signup_url="https://forms.gle/abc") == 1
    page = (out / "index.html").read_text()
    assert 'data-mode="static"' in page and '"signup_url": "https://forms.gle/abc"' in page
    state = (out / "data" / "state.json").read_text()
    detail = (out / "data" / f"show-{sid}.json").read_text()
    assert "Galileo" in state and '"history"' in detail
    everything = page + state + detail
    assert "secret-friend" not in everything and "images.todaytix.com" not in everything

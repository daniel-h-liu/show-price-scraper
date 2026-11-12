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

"""Turns stored checks into what the digest and the web page show: per show, the latest prices for every
upcoming performance, each with its usual price (the average of earlier days) and a deal flag."""
from __future__ import annotations

from dataclasses import dataclass, field

from scraper import db
from scraper.models import now_local

DEAL_FRACTION = 0.8   # a date is a deal when it's at least 20% below its own usual price...
MIN_HISTORY_DAYS = 2  # ...and that usual price comes from at least this many earlier days


@dataclass
class Perf:
    performance: str
    price_cents: int
    detail: str | None
    usual_cents: int | None      # average of earlier days' prices for this same performance
    history_days: int
    previous_cents: int | None   # the most recent earlier day's price

    @property
    def deal(self) -> bool:
        return (self.usual_cents is not None and self.history_days >= MIN_HISTORY_DAYS
                and self.price_cents <= self.usual_cents * DEAL_FRACTION)

    @property
    def change_cents(self) -> int | None:
        return None if self.previous_cents is None else self.price_cents - self.previous_cents

    def to_dict(self) -> dict:
        return dict(performance=self.performance, price_cents=self.price_cents, detail=self.detail,
                    usual_cents=self.usual_cents, history_days=self.history_days,
                    previous_cents=self.previous_cents, change_cents=self.change_cents, deal=self.deal)


@dataclass
class ShowSummary:
    id: int
    name: str
    url: str
    site: str
    image_url: str | None
    status: str | None = None          # latest check's status (None = never checked)
    reason: str | None = None
    checked_at: str | None = None
    last_good_at: str | None = None    # when shown prices were fetched, if the latest check failed
    fail_days: int = 0                 # days in a row the latest check of the day failed
    perfs: list[Perf] = field(default_factory=list)   # upcoming, sorted by date
    trend: list[tuple[str, int]] = field(default_factory=list)  # (day, cheapest upcoming)

    @property
    def cheapest(self) -> list[Perf]:
        return sorted(self.perfs, key=lambda p: (p.price_cents, p.performance))

    @property
    def low_change_cents(self) -> int | None:
        """Cheapest-upcoming today vs the previous day it was recorded."""
        return self.trend[-1][1] - self.trend[-2][1] if len(self.trend) >= 2 else None

    def to_dict(self) -> dict:
        return dict(id=self.id, name=self.name, url=self.url, site=self.site, image_url=self.image_url,
                    status=self.status, reason=self.reason, checked_at=self.checked_at,
                    last_good_at=self.last_good_at, fail_days=self.fail_days, perfs=[p.to_dict() for p in self.perfs],
                    trend=self.trend, low_change_cents=self.low_change_cents,
                    cheapest=[p.to_dict() for p in self.cheapest[:5]])


def summarize(con, show) -> ShowSummary:
    s = ShowSummary(show["id"], show["name"], show["url"], show["site"], show["image_url"])
    last = db.latest_check(con, show["id"])
    if last is None:
        return s
    s.status, s.reason, s.checked_at = last["status"], last["reason"], last["checked_at"]
    s.fail_days = db.fail_streak_days(con, show["id"])
    good = db.latest_good_check(con, show["id"])
    if good is None:
        return s
    if good["id"] != last["id"]:
        s.last_good_at = good["checked_at"]
    today = good["checked_at"][:10]
    history = db.daily_prices(con, show["id"], before=today)  # earlier days only
    now = now_local()
    for row in db.check_prices(con, good["id"]):
        if row["performance"] <= now:
            continue
        past = history.get(row["performance"], [])
        usual = round(sum(p for _, p in past) / len(past) / 100) * 100 if past else None  # whole dollars
        s.perfs.append(Perf(row["performance"], row["price_cents"], row["detail"], usual, len(past),
                            past[-1][1] if past else None))
    s.trend = db.cheapest_over_time(con, show["id"])
    return s


def all_summaries(con) -> list[ShowSummary]:
    return [summarize(con, sh) for sh in db.shows(con)]


def detail(con, show) -> dict:
    """Everything the page's per-show view needs: the summary plus each upcoming performance's history."""
    d = summarize(con, show).to_dict()
    upcoming = {p["performance"] for p in d["perfs"]}
    d["history"] = {perf: days for perf, days in db.daily_prices(con, show["id"]).items() if perf in upcoming}
    return d

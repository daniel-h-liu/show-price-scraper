"""TodayTix: headless. The show page fetches api.todaytix.com/api/v3/events/<id>/occurrences,
one entry per performance with fromPrice (cents) and quantityAvailable."""
from __future__ import annotations

import re

from scraper.models import Offer, ParseResult, to_local

NAME = "todaytix"
BROWSER = "headless"
URL_RE = re.compile(r"todaytix\.com/[^/]+/shows/(\d+)")


def matches(url: str) -> bool:
    return bool(URL_RE.search(url))


def is_data_response(url: str, show_url: str) -> bool:
    m = URL_RE.search(show_url)
    return bool(m) and f"/api/v3/events/{m.group(1)}/occurrences" in url


def parse(body: dict) -> ParseResult:
    occurrences = [o for o in body.get("data") or [] if o.get("startsAt")]
    offers = []
    for o in occurrences:
        price = (o.get("fromPrice") or {}).get("amount")
        qty = o.get("quantityAvailable") or 0
        if price is None or qty <= 0:
            continue
        offers.append(Offer(price_cents=int(price), performance=to_local(o["startsAt"]),
                            detail=f"{qty} left" if qty < 10 else None))
    return ParseResult(offers=offers, seen=len(occurrences))


def parse_captured(bodies: list[dict]) -> ParseResult | None:
    """Latest occurrences response, or None if none was captured."""
    usable = [b for b in bodies if isinstance(b.get("data"), list)]
    return parse(usable[-1]) if usable else None

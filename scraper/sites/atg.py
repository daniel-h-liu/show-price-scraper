"""ATG Tickets (us.atgtickets.com): headless. The calendar page posts a GraphQL query to
calendar-service.core.platform.atgtickets.com; data.getShow.show.performances[] has
price.minPrice (dollars, before fee) and availabilityStatus (GOOD / MEDIUM / LOW seen so far)."""
from __future__ import annotations

import re

from scraper.models import Offer, ParseResult, to_local

NAME = "atg"
BROWSER = "headless"
URL_RE = re.compile(r"atgtickets\.com/events/")
# Statuses observed with live, priced performances. Anything else is treated as not purchasable.
AVAILABLE_STATUSES = {"GOOD", "MEDIUM", "LOW", "AVAILABLE", "LIMITED"}


def matches(url: str) -> bool:
    return bool(URL_RE.search(url))


def is_data_response(url: str, show_url: str) -> bool:
    return "calendar-service" in url and "atgtickets.com" in url


def performances(body: dict) -> list | None:
    try:
        return body["data"]["getShow"]["show"]["performances"]
    except (KeyError, TypeError):
        return None


def parse(body: dict) -> ParseResult:
    perfs = [p for p in performances(body) or [] if (p.get("dates") or {}).get("performanceDate")]
    offers = []
    for p in perfs:
        price = (p.get("price") or {}).get("minPrice")
        if price is None or p.get("availabilityStatus") not in AVAILABLE_STATUSES:
            continue
        offers.append(Offer(price_cents=round(float(price) * 100),
                            performance=to_local(p["dates"]["performanceDate"]),
                            detail="few left" if p["availabilityStatus"] == "LOW" else None))
    return ParseResult(offers=offers, seen=len(perfs))


def parse_captured(bodies: list[dict]) -> ParseResult | None:
    """The calendar-service response carrying performances (it also answers other queries)."""
    usable = [b for b in bodies if performances(b) is not None]
    return parse(usable[-1]) if usable else None

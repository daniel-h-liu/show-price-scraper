"""Adapter registry. Each adapter module exposes NAME, BROWSER ('headless' | 'chrome'), matches(url), and either
  - is_data_response(response_url, show_url) + parse_captured(bodies): the page's own JSON is captured
    while it loads, or
  - collect(page, url, goto, wait_for): the adapter drives the page itself (Met: many performances)."""
from __future__ import annotations

from urllib.parse import urlparse

from scraper.sites import atg, metopera, todaytix

ADAPTERS = [todaytix, atg, metopera]

# Tested Nov 2026 and blocked even in real Chrome; reported up front instead of loading the page.
KNOWN_BLOCKED = {
    "telecharge.com": "Telecharge blocks automated browsers (Akamai 403) -- track the show via TodayTix instead",
    "tickets.broadwaydirect.com": "Broadway Direct blocks automated browsers (Cloudflare challenge) -- track the show via TodayTix instead",
}


def domain(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def adapter_for(url: str):
    return next((a for a in ADAPTERS if a.matches(url)), None)


def check_supported(url: str) -> str | None:
    """None if we can track this URL, else a human-readable reason."""
    d = domain(url)
    if d in KNOWN_BLOCKED:
        return KNOWN_BLOCKED[d]
    if adapter_for(url) is None:
        return (f"{d or url} isn't supported. Supported: TodayTix show pages, ATG calendar pages, "
                "and Met Opera SmartSeat pages (smartseat?itemNumber=...)")
    return None

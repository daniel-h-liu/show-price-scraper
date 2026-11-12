"""Refresh the show picker's catalog from TodayTix's NYC category pages (headless). The page embeds its
show list in __NEXT_DATA__ (props.pageProps.productList.results.shows).

    python -m scraper.catalog     # refresh now
"""
from __future__ import annotations

import json
import re
import sys

from scraper import db
from scraper.fetch import UA
from scraper.models import now_local

CATEGORIES = {"broadway-shows": "Broadway", "off-broadway-shows": "Off-Broadway"}
PAGE = "https://www.todaytix.com/nyc/category/{}"


def _image(o: dict) -> str | None:
    media = ((o.get("images") or {}).get("productMedia") or {})
    for key in ("posterImageSquare", "posterImage", "appHeroImage"):
        url = (((media.get(key) or {}).get("file") or {}).get("url"))
        if url:
            return ("https:" + url if url.startswith("//") else url) + "?w=240&fm=webp"
    return None


def parse_category(next_data: dict, category: str, updated_at: str) -> tuple[list[dict], int | None]:
    """(catalog rows, total the site says exist) from one category page's __NEXT_DATA__."""
    results = next_data["props"]["pageProps"]["productList"]["results"]
    rows = []
    for o in results.get("shows") or []:
        name = (o.get("displayName") or o.get("name") or "").strip()
        if not o.get("id") or not name:
            continue
        # a few shows (Hamilton, Book of Mormon) come without a slug; TodayTix routes on the id anyway
        slug = o.get("slug") or re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        price = (o.get("lowPriceForRegularTickets") or {}).get("value")
        rows.append(dict(
            url=f"https://www.todaytix.com/{o.get('locationSeoName') or 'nyc'}/shows/{o['id']}-{slug}",
            name=name,
            category=category, image_url=_image(o),
            from_price_cents=round(price * 100) if price is not None else None,
            venue=o["venue"] if isinstance(o.get("venue"), str) else (o.get("venue") or {}).get("name"),
            updated_at=updated_at))
    total = (results.get("pagination") or {}).get("total")
    return rows, total


def refresh(con) -> str:
    from playwright.sync_api import sync_playwright
    stamp = now_local()
    rows: dict[str, dict] = {}
    notes = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(user_agent=UA).new_page()
        try:
            for slug, label in CATEGORIES.items():
                page.goto(PAGE.format(slug), wait_until="domcontentloaded", timeout=60_000)
                data = json.loads(page.eval_on_selector("#__NEXT_DATA__", "e => e.textContent"))
                found, total = parse_category(data, label, stamp)
                for r in found:
                    rows.setdefault(r["url"], r)  # Broadway wins if a show is in both
                notes.append(f"{label}: {len(found)}" + (f" of {total}" if total and total > len(found) else ""))
        finally:
            browser.close()
    if not rows:
        raise RuntimeError("TodayTix catalog came back empty; keeping the old one")
    db.replace_catalog(con, list(rows.values()))
    return ", ".join(notes)


if __name__ == "__main__":
    with db.connect() as con:
        print("catalog refreshed:", refresh(con))
    sys.exit(0)

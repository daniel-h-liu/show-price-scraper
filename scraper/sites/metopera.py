"""Met Opera SmartSeat: real Chrome (Cloudflare blocks headless).

Any SmartSeat URL for a production works as the tracked URL: the page embeds the production's full
performance list (productionPerformances: id, date) and the venue's levels (each with a seatmapId).
The page POSTs api/seating/GetSeatmap {itemId, seatMapId, ...} to draw a level; we send that same
request from inside the page for every upcoming performance x level. Each response has levelSeats[]
(isAvailable, zoneId) and allSeatPricing[] (zoneId -> price in dollars)."""
from __future__ import annotations

import json
import re
from datetime import datetime

from scraper.models import NYC, Offer, ParseResult, to_local

NAME = "metopera"
BROWSER = "chrome"
URL_RE = re.compile(r"metopera\.org/smartseat\?.*itemNumber=(\d+)", re.I)
PERF_RE = re.compile(r'\{"id":(\d+),"title":"((?:[^"\\]|\\.)*)","date":"([^"]+)"')


def matches(url: str) -> bool:
    return bool(URL_RE.search(url))


def performances_from_html(html: str) -> list[tuple[int, str, str]]:
    """[(item id, title, local datetime)] from the embedded productionPerformances JSON (it is
    string-escaped inside the page, so unescape quotes first)."""
    text = html.replace('\\"', '"')
    seen, out = set(), []
    for m in PERF_RE.finditer(text):
        pid = int(m.group(1))
        if pid not in seen:
            seen.add(pid)
            out.append((pid, m.group(2), to_local(m.group(3))))
    return out


EXCLUDED_ZONES = ("score desk",)  # music-desk seats with little or no stage view; always the cheapest


def excluded(*labels: str | None) -> bool:
    return any(x in (l or "").lower() for l in labels for x in EXCLUDED_ZONES)


def parse_level(body: dict, performance: str | None) -> ParseResult:
    """One GetSeatmap response (one level): the price of each zone with an available seat.
    Score Desks are skipped (standing room is kept)."""
    zone_price = {}
    for z in body.get("allSeatPricing") or []:
        prices = [p["price"] for p in z.get("prices") or [] if p.get("price") is not None]
        if prices:
            zone_price[z["zoneId"]] = (min(prices), z.get("description"))
    seats = body.get("levelSeats") or []
    by_zone: dict[int, Offer] = {}
    for s in seats:
        t = s.get("tessituraSeat") or {}
        if not t.get("isAvailable") or t.get("zoneId") not in zone_price:
            continue
        price, desc = zone_price[t["zoneId"]]
        section, zone = t.get("sectionDescription"), s.get("zoneDescription") or desc
        if excluded(section, zone, desc):
            continue
        label = zone if not section or section == zone or (zone or "").startswith(section) else f"{section} — {zone}"
        by_zone[t["zoneId"]] = Offer(round(price * 100), performance, label)
    return ParseResult(offers=list(by_zone.values()), seen=len(seats))


def parse_performance(bodies: list[dict], performance: str | None) -> ParseResult:
    """All captured levels of one performance (repeat responses for a level are ignored)."""
    out, levels = ParseResult(), set()
    for b in bodies:
        if b.get("levelId") in levels:
            continue
        levels.add(b.get("levelId"))
        r = parse_level(b, performance)
        out.offers += r.offers
        out.seen += r.seen
    return out


LEVEL_RE = re.compile(r'"availableCount":(\d+),"seatmapId":"([0-9a-f-]{36})"')
FETCH_JS = """async (body) => {
  const r = await fetch('/api/seating/GetSeatmap', {method: 'POST', credentials: 'same-origin',
    headers: {'Content-Type': 'application/json', 'Accept': 'application/json'}, body: JSON.stringify(body)});
  return [r.status, await r.text()];
}"""
PAUSE_MS = 700


def level_seatmap_ids(html: str) -> list[str]:
    """The venue's per-level seat map ids, from the level list embedded in the page."""
    out = []
    for _, sid in LEVEL_RE.findall(html.replace('\\"', '"')):
        if sid not in out:
            out.append(sid)
    return out


def collect(page, url: str, goto, wait_for) -> ParseResult | str:
    """Load one SmartSeat page (real Chrome), then request every upcoming performance's seat map level by
    level, using the same request the page sends when you pick a level (only itemId and seatMapId
    change). Returns a ParseResult, or a failure reason string."""
    template: list[dict] = []

    def on_request(r):
        if not template and r.url.split("?")[0].endswith("/api/seating/GetSeatmap") and r.post_data:
            try:
                template.append(json.loads(r.post_data))
            except ValueError:
                pass

    page.on("request", on_request)
    blocked = goto(url)
    if blocked and not wait_for(lambda: bool(template), 20_000):
        return blocked
    if not wait_for(lambda: bool(template), 30_000):
        return "seat map never requested (site layout or API may have changed)"
    page.wait_for_timeout(1500)  # let the page settle before adding our own requests

    html = page.content()
    perfs = performances_from_html(html)
    levels = level_seatmap_ids(html)
    if not perfs or not levels:
        return "couldn't find the production's performances or seating levels on the page"
    now = datetime.now(NYC).replace(tzinfo=None).isoformat(timespec="minutes")
    upcoming = [(pid, when) for pid, _, when in perfs if when > now]

    def seatmap(pid: int, sid: str) -> dict | None:
        body = {**template[0], "itemId": pid, "seatMapId": sid}
        for attempt in range(2):
            status, text = page.evaluate(FETCH_JS, body)
            page.wait_for_timeout(PAUSE_MS)
            if status == 200 and text.strip():
                try:
                    return json.loads(text)
                except ValueError:
                    pass
            page.wait_for_timeout(3000 * (attempt + 1))
        return None

    out, failures, loaded_any = ParseResult(), [], False
    for pid, when in upcoming:
        bodies = [b for b in (seatmap(pid, sid) for sid in levels) if b is not None]
        if len(bodies) < len(levels):
            failures.append(f"{when[:10]} ({len(levels) - len(bodies)} of {len(levels)} levels didn't load)")
        if not bodies:
            continue
        loaded_any = True
        r = parse_performance(bodies, when)
        out.offers += r.offers
        out.seen += r.seen
    if failures:
        out.note = "incomplete: " + "; ".join(failures)
    if upcoming and not loaded_any:
        return "no seat maps loaded: " + "; ".join(failures)
    return out

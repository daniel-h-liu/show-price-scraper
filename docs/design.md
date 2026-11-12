# show-price-scraper — design (updated Nov 11, 2026)

Picks up from `scraper-agent-handoff-nov10.md`.

## What it is

A **daily price digest plus a local page**. It does not alert on thresholds: Met pricing is
mostly fixed, while TodayTix and Broadway prices move daily, and the user couldn't name a
threshold in advance. Instead:

- **Pick shows** on a local page (http://localhost:8765, started on demand with
  `scripts/page.sh`; nothing but the daily job runs in the background): a TodayTix catalog with Track
  toggles, plus a box for pasting supported URLs (needed for the Met).
- **Every day at 08:00** (launchd): refresh the catalog, check every performance of every
  tracked show, store it all, and email a digest.
- **Digest**: per show, the 5 cheapest upcoming dates with today's price, the date's usual
  price, and a Deal flag.
- **Page**: per show, today's price for every performance (chart and table), the cheapest
  upcoming price over time, and the price history of any one performance (click it).

$0 running cost: no LLM, and code written by hand for each site.

## Site adapters

| Site | Browser | Data source |
|---|---|---|
| TodayTix | headless | the page's own `api.todaytix.com/api/v3/events/<id>/occurrences` JSON: `fromPrice` + `quantityAvailable` per performance |
| ATG | headless | the page's own calendar-service GraphQL JSON: `performances[].price.minPrice` + `availabilityStatus` |
| Met Opera | real Chrome, visible (Cloudflare blocks headless) | Load one SmartSeat page. It embeds the production's performances and the venue's level `seatmapId`s. For each upcoming performance × level, send the same `api/seating/GetSeatmap` POST the page sends when you pick a level (700 ms apart, one retry). The cheapest available seat's zone price is the result; Score Desks are excluded (always $23, little or no stage view), standing room is kept. |

The Met adapter originally clicked the level radio buttons in the page. That was unreliable:
clicks were silently ignored after navigation, and fast loads got empty responses. Sending
the page's own request is deterministic and needs only one page load.

The catalog comes from TodayTix's `/nyc/category/{broadway-shows,off-broadway-shows}`
`__NEXT_DATA__`. Those pages embed at most 54 shows, so off-Broadway is partial; other
shows can be pasted.

Blocked (tested, even in real Chrome): Telecharge (Akamai) and Broadway Direct
(Cloudflare challenge). No stealth plugins and no CAPTCHA solving.

## Contract

`CheckResult(status, performances[(price_cents, performance, detail)], reason, seen, checked_at)`,
with `status ∈ available | sold_out | no_inventory | scrape_failed`. The rule from the old
repo still applies, enforced when a result is built: **only `available` carries prices,
and it must carry at least one**. `scrape_failed` needs a reason and is never guessed
around. The page and the digest keep showing the last good prices, labelled with their
age.

All times are naive New York local ISO strings (every show is in NYC).

## Storage — `data/tracker.db` (SQLite)

`shows` (tracked; untracking just deactivates, so history is kept), `checks` (one per
show per run), `prices` (one row per purchasable performance per check), and `catalog`.

## Usual price and Deal

For each upcoming performance: **usual** = the mean of its prices on *earlier days*, one
value per day (the last check that day), rounded to whole dollars. It's a **Deal** when
today's price is ≤ 80% of usual and there are at least 2 earlier days.

## Concurrency

Checks started from the page run as `python -m scraper.run --show-id N` subprocesses. Every
run takes `data/run.lock`, so the daily job and manual checks never drive the browsers (or
the Chrome profile) at the same time.

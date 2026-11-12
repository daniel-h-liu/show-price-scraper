# show-price-scraper

Tracks ticket prices for the NYC shows you pick. Every morning at 8 it checks every
performance of every tracked show and emails a digest: the 5 cheapest upcoming dates per
show, what each date usually costs, and a **Deal** flag when a date is at least 20% below
its own usual price. Only the daily job is scheduled. When you want to pick shows or browse
each one's full calendar and price history, run `scripts/page.sh`. It opens
**http://localhost:8765**; Ctrl-C stops it.

Free to run: no LLM and no paid APIs. Each supported site has its own hand-written
adapter (see `docs/design.md`).

## Supported sites

| Site | How to add | Notes |
|---|---|---|
| TodayTix | Pick from the catalog on the page, or paste `todaytix.com/nyc/shows/<id>-...` | Catalog = all Broadway shows plus the first 54 off-Broadway shows TodayTix lists. Covers shows officially sold by Telecharge and Broadway Direct. |
| ATG Tickets | Paste `us.atgtickets.com/events/<show>/<theatre>/calendar/` | Most ATG shows are also in the TodayTix catalog |
| Met Opera | Paste **any** SmartSeat link for the production (`metopera.org/smartseat?itemNumber=<n>`) | Checks every upcoming performance of that opera. Opens a visible Chrome window for about a minute (Cloudflare blocks headless browsers). Score Desks (little or no stage view) are ignored; standing room counts. |

Telecharge and Broadway Direct block automated browsers, so the page rejects their URLs
and points you to TodayTix.

## How "usually" and "Deal" work

Every check stores the cheapest price for every performance. A date's **usual** price is
the average of its prices on earlier days (one price per day, the last check that day). It
is flagged **Deal** when today's price is ≤ 80% of that, with at least 2 earlier days of
history. Prices are each site's listed price before fees.

## Commands

```bash
scripts/install_launchd.sh                      # 8 AM daily check + digest (--uninstall to remove)
scripts/page.sh                                 # open the tracker page; Ctrl-C to stop
.venv/bin/python -m scraper.run                 # check everything now and email the digest
.venv/bin/python -m scraper.run --no-email      # check everything, no email
.venv/bin/python -m scraper.run --digest-only   # email a digest from stored data
.venv/bin/python -m scraper.run --test-email    # verify Gmail settings
.venv/bin/python -m scraper.catalog             # refresh the TodayTix catalog
.venv/bin/python -m pytest -q                   # tests
```

Setup from scratch: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt &&
.venv/bin/playwright install chromium`, then copy `.env.example` to `.env` and fill in a
Gmail app password. Google Chrome must be installed for the Met.

Data: `data/tracker.db` (SQLite). Logs: `logs/run.log`, `logs/web-jobs.log`.

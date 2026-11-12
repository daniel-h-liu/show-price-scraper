# show-price-scraper: handoff for the personal-website integration (Nov 11, 2026)

For the AI building the owner's personal website, which will feature this project. It covers what the project
is, what already exists publicly, the data you can use, the rules to keep, and material for a project write-up.
Read `README.md` and `docs/design.md` next; they are kept current with the code.

## 1. What the project is

A personal ticket-price tracker for NYC shows (Broadway, off-Broadway, Met Opera). Every morning at 8 it
checks the cheapest ticket for **every upcoming performance** of each tracked show, stores everything in SQLite,
and emails a digest:
- the 5 cheapest upcoming dates per show
- what each date usually costs (the average of that same performance's price on earlier days)
- a **Deal** flag when a date is at least 20% below its usual price, with 2+ earlier days of history

A local page lets the owner pick shows and browse each show's calendar and price history.

It deliberately sends no threshold alerts. Met prices are mostly fixed while TodayTix and Broadway prices move
daily, so the owner wanted a daily overview to decide whether to buy, not "tell me when it's under $X".

It costs $0 to run: there is no LLM and no paid API, and each site has a hand-written scraper. Everything runs
on the owner's Mac.

## 2. What already exists publicly (the read-only copy is DONE)

| Thing | Where |
|---|---|
| Public repo | https://github.com/daniel-h-liu/show-price-scraper (branch `main`) |
| Read-only page (GitHub Pages) | https://daniel-h-liu.github.io/show-price-scraper/ |
| Its data | `https://daniel-h-liu.github.io/show-price-scraper/data/state.json` and `.../data/show-<id>.json` |
| What builds it | `scraper/publish.py`, run automatically after each daily check |

How the public copy works:
- It's the same `scraper/static/index.html` as the local page, run in read-only mode: `publish.py` adds
  `window.STATIC = {signup_url, repo_url}` and `data-mode="static"` to the page.
- The page reads pre-built JSON. It has no controls, no catalog, no poster images, and no recipient data.
- Each publish **force-pushes one fresh commit** to `gh-pages`. **Never edit `gh-pages` by hand**: the next
  morning's run overwrites it.
- The JSON is served with `Access-Control-Allow-Origin: *` and `Cache-Control: max-age=600`, so another site
  can `fetch()` it directly from the browser.
- The "Sign up" button opens the owner's Google Form, set as `SIGNUP_URL` in the owner's `.env`. The owner
  adds the people they approve on the local page. Signing up does not add anyone automatically.

So "create a read-only copy" is already done. **Your job is to feature it on the personal site.**

## 3. Integration options (pick with the owner)

1. **Link plus write-up (simplest, recommended to start).** A project page on the personal site with a
   description, screenshots (§7), the engineering story (§6), and links to the live page and the repo.
2. **Embed the live page.** `<iframe src="https://daniel-h-liu.github.io/show-price-scraper/" ...>`. It is
   responsive down to phone width (16px side gutters, no horizontal scroll) and supports light and dark mode
   through `prefers-color-scheme`.
3. **Native widget using the JSON.** Fetch `data/state.json` from the personal site and render a small
   "today's cheapest" card in the site's own style. Use the schema in §4. Show `generated_at` so stale data is
   obvious. If the owner's Mac is off, the data simply stops updating.
4. **Custom domain** (optional). GitHub Pages can serve the existing page at e.g.
   `shows.<owner-domain>`. Add a `CNAME` through Pages settings. Because `publish.py` force-pushes `gh-pages`
   daily, the domain must be set in the repo's Pages settings, or `publish.build()` must be taught to
   write a `CNAME` file. Otherwise the daily push would wipe it.

Whatever you build must only read the published static files. **Never call the ticket sites (TodayTix, the
Met, ATG) from the personal site or from visitors' browsers.** Scraping volume is what gets the owner's
IP blocked, and the design keeps it at once a day from one machine.

## 4. Public data schema

All times are naive New York local ISO strings (`"2026-12-23T20:00"`; days `"2026-11-11"`). Money is
integer cents.

`data/state.json`
```jsonc
{
  "generated_at": "2026-11-11T22:21:57",
  "running": [], "ended": [],          // always empty in the public copy
  "shows": [{
    "id": 1, "name": "Galileo", "site": "todaytix",          // site: todaytix | atg | metopera
    "url": "https://www.todaytix.com/nyc/shows/...",         // buy link
    "image_url": null,                                         // always null publicly (posters are TodayTix's)
    "status": "available",            // available | sold_out | no_inventory | scrape_failed | null (never checked)
    "reason": null,                   // failure reason or partial-load note
    "checked_at": "2026-11-11T17:02", "last_good_at": null,  // last_good_at set when today's check failed
    "fail_days": 0,                   // consecutive days failing
    "perfs":    [Perf, ...],          // every upcoming performance, by date
    "cheapest": [Perf, ...],          // top 5 by price
    "trend": [["2026-11-11", 6200], ...],   // cheapest upcoming price per day checked
    "low_change_cents": null          // trend's last minus previous day
  }]
}
```
`Perf` = `{performance, price_cents, detail, usual_cents, history_days, previous_cents, change_cents, deal}`.
`detail` is site-specific: "3 left" on TodayTix, or the seat zone ("Family Circle Boxes") at the Met.

`data/show-<id>.json` = the same show object plus `history`: `{ "<performance>": [["<day>", price_cents], ...] }`,
with one entry per upcoming performance.

## 5. Rules and constraints (from the owner; keep them)

- **$0 running cost.** No LLM APIs and no paid services.
- **No stealth plugins or CAPTCHA solving.** Telecharge (Akamai) and Broadway Direct (Cloudflare) block
  automated browsers even in real Chrome. That's accepted: their shows are tracked through TodayTix instead.
- **Privacy:**
  - Never publish digest recipients, the owner's email, or `.env` contents.
  - Commits to this repo use the owner's GitHub no-reply address (set in the repo's git config). Don't
    commit with a personal email.
  - Keep poster images off the public page.
- **No threshold alerts.** "Deal" is derived from each performance's own history.
- **Met prices exclude Score Desks** (music-desk seats with little or no stage view, always the cheapest).
  Standing room and box seats count; the owner explicitly chose to keep the boxes.
- **Wording:** the public page should read as a personal tracker. Prices are before fees, each card links to
  the real ticket page, and the sign-up text says the digest only covers shows the owner tracks.

## 6. How it works (for the write-up)

```
launchd 08:00 ─▶ scraper.run ─▶ refresh TodayTix catalog ─▶ check each tracked show ─▶ SQLite
                                                                          │
                     owner digest + friends' digests (Gmail SMTP) ◀───────┤
                     scraper.publish ─▶ .site/ ─▶ force-push gh-pages ◀────┘
scripts/page.sh ─▶ local page (127.0.0.1:8765): pick shows, recipients, charts
```

| Site | How prices are obtained |
|---|---|
| TodayTix | Headless Chromium loads the show page. The scraper captures the page's own `api/v3/events/<id>/occurrences` JSON: `fromPrice` and `quantityAvailable` per performance. |
| ATG | Headless Chromium captures the page's calendar GraphQL response: `price.minPrice` and `availabilityStatus` per performance. |
| Met Opera | Cloudflare blocks headless browsers, so this uses **real Chrome** (visible, persistent profile). The scraper loads one SmartSeat page, which embeds the production's performances and the venue's seating-level ids. For each performance × level, it sends the same `GetSeatmap` POST the page itself sends, from inside the page. Clicking through the UI was flaky: it silently missed nights. Replaying the page's own request is deterministic and takes about a minute for 8 nights. |

Design points worth telling:
- **One strict result contract.** Only an `available` result carries prices, and it must carry at least one. A
  failure needs a reason and is never guessed around. The page and digest keep showing the last good prices
  with their date.
- **"Usual" price per performance, not per show.** Each date is compared to its own past, so surge pricing on
  a Saturday doesn't make a Tuesday look like a deal.
- **Reliability:**
  - The digest opens with "Needs attention" after 2+ failed days.
  - A crashed run emails the owner a failure report instead of staying silent.
  - Shows whose performances have all passed stop being checked automatically.
- **One run at a time:** a file lock (`data/run.lock`) keeps the daily job and manual checks from driving the
  browsers at the same time.
- **Scraping survey:** about half of Broadway sells through Telecharge, which is blocked. TodayTix covers at
  least 22 of the 27 blocked shows that were checked.
- **Stack:**
  - Python 3.12 and Playwright
  - SQLite, stdlib `http.server`, and vanilla JS with hand-rolled SVG charts (no frameworks)
  - Gmail SMTP and launchd
  - GitHub Pages
  - 33 pytest tests

## 7. Screenshots and assets to capture

Use `scripts/page.sh` locally (it includes posters) or the public page (no posters):
- The tracked-show cards: cheapest price, change vs previous day, sparkline, 5 cheapest-date chips, Deal tags.
- "Calendar & history" opened: the price-by-performance dot chart, the price-over-time chart, and the table.
  Clicking a date shows that night's own price history.
- Dark mode, and phone width.
- The digest email: the owner version, and a friends' version (`.venv/bin/python -m scraper.run --friend-preview`
  emails the owner a copy).
- A short GIF: open a show, click a date, watch the history chart switch.

The charts look much better after a week or more of daily history. Tracking started Nov 11, 2026.

## 8. Operating facts you may need

- Everything runs on the owner's Mac. If it's asleep at 8 AM, launchd runs the job on wake; if it's off, the
  public data goes stale. `generated_at` and each show's `checked_at` show how old it is.
- Install or uninstall the schedule: `scripts/install_launchd.sh [--uninstall]`. It sets up only the daily job;
  the local page runs on demand.
- Logs: `logs/run.log` (daily job, including publish results) and `logs/web-jobs.log`.
- `.env` keys: `GMAIL_USER`, `GMAIL_APP_PASSWORD`, `ALERT_TO`, `PUBLIC_URL`, `SIGNUP_URL`.
- Publishing pushes over HTTPS using the `gh` CLI's stored login, so it works under launchd.
- Rebuild without pushing: `.venv/bin/python -m scraper.publish --no-push`. This writes `.site/`; serve that
  folder with any static server to preview.

## 9. Known limits and candidate next features

- The TodayTix catalog lists at most 54 off-Broadway shows (no page-2 parameter found). Other shows can be
  added by pasting their URL.
- Prices are before fees. TodayTix fees can add 20–30%, and it's unknown whether fees show before checkout.
- Ideas the owner liked but hasn't asked for yet:
  - **"My available dates"**, so the 5 cheapest are dates the owner can actually attend.
  - **Per-show seat exclusions**, generalizing the Score Desk rule.

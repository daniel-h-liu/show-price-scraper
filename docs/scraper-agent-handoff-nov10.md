# Headless-browser scraper agent — handoff note (Nov 10, 2026)

Written at the point of deliberately splitting off into a new chat and a
new project. Not a design doc — a record of what was decided here, so
the new session doesn't have to be re-derived from scratch.

## Why this split off from show-price-tracker

This repo is stdlib-only by deliberate choice, with a typed contract
(adapter -> RawQuote, a known event id in, a structured quote out) built
around APIs. A scraping agent that visits arbitrary URLs and interprets
pages per free-text instructions is a different kind of system — a real
browser-automation dependency, plus an LLM doing interpretive extraction
rather than deterministic parsing. It deserves its own repo and its own
judgment, not a fork of this one's constraints.

## What prompted it

Both "free, self-serve, no-approval" ticket-price APIs were empirically
tested against this project's actual target shows and came back empty:

- **SeatGeek**: `stats` (lowest_price, listing_count) is empty `{}` for
  every event tested, across every category (NFL, concerts, Broadway,
  opera) -- even events the API's own `listing_count.gt=0` filter says
  have listings. Reads as a free-tier account restriction, not a bug or
  a per-event quirk.
- **Ticketmaster Discovery API**: `priceRanges` is NOT dead the same way
  -- it populated for 2 of 20 small, far-future, clearly-not-sold-out
  concerts. But for Met Opera (0 of 20) and Hamilton (0 of 10) -- the
  actual target shows -- it's reliably empty. The field only reflects
  *primary* inventory still available; popular shows sell out of that
  within minutes to hours, so there's structurally nothing to show.

Remaining non-scraping options (StubHub/Vivid Seats/Ticketmaster
Inventory Status partner programs) all require an email-and-approval
process, possibly paid, with no guaranteed timeline. Given that, the
decision was to try headless browsing instead and see how far it
actually gets before something blocks it.

## What the user specified for the scraper agent

- **Input is a list of URLs**, not structured event ids -- shows can be
  on "weird websites" (named example: Symphony Space) or a venue's own
  site, not just major ticketing platforms.
- **Per-URL, optionally attach instructions** -- a prompt telling the
  agent how to handle that specific site (where to look, what to click
  past, what counts as the price). Different sites need different
  handling; this is explicit, not inferred.
- **Failure is a valid, expected outcome, not an error to avoid** -- if a
  site can't be scraped (blocked, CAPTCHA, layout the agent can't parse),
  return that as a status, never fabricate a price. This already matches
  how this repo's existing pipeline treats a missing price (see below).
- **Output, for now, is simplified to "cheapest"** -- the agent's job per
  URL is: find the lowest available price (and implicitly, availability
  -- is anything for sale at all). Seat-level detail, multiple price
  tiers, etc. are explicitly deferred ("for now, always aiming for
  cheapest to simplify the problem").
- **Three things to build, in the user's own framing**: (1) set up the
  browser itself, (2) the inputs given to it (URL + optional per-site
  instructions), (3) the common framework/schema the agent reports back
  in (price + availability, consistently shaped regardless of source).

## One existing contract worth carrying over, if useful

Not a requirement -- just worth knowing it already exists and was
deliberately designed, in case the new project wants the same shape for
consistency (e.g. if the scraper's output is ever meant to feed back
into this repo's pipeline later):

```python
class Availability(str, Enum):
    AVAILABLE = "available"
    SOLD_OUT = "sold_out"
    NO_INVENTORY = "no_inventory"
```

The hard rule this repo built everything around: **availability is a
state, not a price** -- a quote with a non-AVAILABLE state is never
allowed to carry a price value (enforced at construction time in this
repo's `RawQuote`, not left to convention). The scraper agent's "blocked
/ couldn't scrape" outcome is a natural fourth state in that same spirit
-- the new project can decide its own name for it.

## Tool-availability note

Whether the new session can drive a real browser directly depends on
what's enabled there (Claude-in-Chrome / computer-use, if the harness
offers them) -- that's a session setting, not guaranteed by starting a
new chat. The fallback that works in any plain Claude Code session is
installing Playwright (or similar) via Bash and writing the automation
in code. Worth confirming which applies before designing around one or
the other.

## Not yet decided (deliberately left for the new session's brainstorm)

- Exact output schema fields beyond "price + availability + status"
- How per-site instructions are authored/stored (inline per URL? a
  separate config file? something richer?)
- Whether/how this ever reconnects to show-price-tracker's existing
  pipeline, dashboard, and email alerts, or stands alone
- Retry/rate-limiting policy per site, and what "got blocked" should do
  on the next scheduled run (same question this repo already answered
  for APIs: don't retry in a loop, let the next run try again -- worth
  deciding afresh for scraping, where the risk profile is different)

"""Load one show URL in the right browser and return a CheckResult.
Every failure becomes a scrape_failed result with a reason; nothing here raises to the caller."""
from __future__ import annotations

import json
import time
from contextlib import contextmanager
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from scraper.models import CheckResult, ParseResult, build_result, failed
from scraper.sites import adapter_for, check_supported

ROOT = Path(__file__).resolve().parent.parent
CHROME_PROFILE = ROOT / ".chrome-profile"
VIEWPORT = {"width": 1366, "height": 900}  # Met's Filters panel (level radios) is open at this width
BLOCK_TITLES = ("just a moment", "access denied", "attention required", "pardon our interruption")
NAV_TIMEOUT_MS = 60_000
DATA_TIMEOUT_MS = 30_000
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/130.0 Safari/537.36")


class Browsers:
    """Opens each browser kind lazily, at most once per run."""

    def __init__(self, pw):
        self._pw = pw
        self._headless = None
        self._chrome = None

    def context(self, kind: str):
        if kind == "headless":
            if self._headless is None:
                browser = self._pw.chromium.launch(headless=True)
                self._headless = browser.new_context(viewport=VIEWPORT, user_agent=UA)
            return self._headless
        if self._chrome is None:
            self._chrome = self._pw.chromium.launch_persistent_context(
                str(CHROME_PROFILE), channel="chrome", headless=False, viewport=VIEWPORT)
        return self._chrome

    def close(self):
        for ctx in (self._headless, self._chrome):
            try:
                if ctx:
                    ctx.close()
            except PlaywrightError:
                pass


@contextmanager
def browsers():
    with sync_playwright() as pw:
        b = Browsers(pw)
        try:
            yield b
        finally:
            b.close()


def fetch(b: Browsers, url: str) -> CheckResult:
    unsupported = check_supported(url)
    if unsupported:
        return failed(unsupported)
    adapter = adapter_for(url)
    try:
        return _fetch_with(b.context(adapter.BROWSER), adapter, url)
    except Exception as e:  # browser crash, closed window, unexpected page -- report, don't raise
        return failed(f"{type(e).__name__}: {str(e).splitlines()[0][:200]}")


def _fetch_with(ctx, adapter, url: str) -> CheckResult:
    page = ctx.new_page()

    def wait_for(cond, timeout_ms):
        deadline = time.monotonic() + timeout_ms / 1000
        while not cond() and time.monotonic() < deadline:
            page.wait_for_timeout(250)
        return cond()

    def goto(target: str) -> str | None:
        """Load a page; return a 'blocked: ...' reason if it looks like a bot wall, else None."""
        resp = page.goto(target, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        status = resp.status if resp else None
        title = (page.title() or "").strip()
        if (status and status >= 400) or title.lower().startswith(BLOCK_TITLES):
            return f"blocked: HTTP {status}, page title {title!r}"
        return None

    try:
        if hasattr(adapter, "collect"):
            r = adapter.collect(page, url, goto, wait_for)
            return failed(r) if isinstance(r, str) else build_result(r)

        captured: list[dict] = []

        def on_response(resp):
            if adapter.is_data_response(resp.url, url):
                try:
                    captured.append(json.loads(resp.text()))
                except Exception:
                    pass

        page.on("response", on_response)
        blocked = goto(url)
        # a challenge page can clear itself in a real browser; give it a chance first
        if blocked and not wait_for(lambda: bool(captured), 20_000):
            return failed(blocked)
        if not wait_for(lambda: bool(captured), DATA_TIMEOUT_MS):
            return failed("price data never loaded (site layout or API may have changed)")
        r: ParseResult | None = adapter.parse_captured(captured)
        if r is None:
            return failed("price data loaded but not in the expected shape")
        return build_result(r)
    finally:
        page.close()

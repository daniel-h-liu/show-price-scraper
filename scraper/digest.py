"""The daily digest email: per tracked show, its 5 cheapest upcoming dates, how today's prices compare with
each date's usual price, and deal flags. Plain text + HTML.

Two versions: the owner's (links to the local page, plus warnings about failing checks and shows that
ended) and the friends' copy (links to the public read-only page if there is one, no maintenance notes)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from html import escape

from scraper.analysis import Perf, ShowSummary
from scraper.models import Status

WEB_URL = "http://localhost:8765"
TOP_N = 5
FAIL_WARN_DAYS = 2  # warn at the top of the owner's digest once a show has failed this many days in a row


def money(c: int | None) -> str:
    if c is None:
        return "—"
    return f"${c / 100:,.0f}" if c % 100 == 0 else f"${c / 100:,.2f}"


def when(perf: str) -> str:
    dt = datetime.fromisoformat(perf)
    t = dt.strftime("%-I:%M %p").replace(":00 ", " ")
    return dt.strftime("%a %b %-d") + f", {t}"


def signed(c: int) -> str:
    return ("▲ +" if c > 0 else "▼ −") + money(abs(c))


def note(p: Perf) -> str:
    bits = []
    if p.deal:
        bits.append(f"DEAL: {round((1 - p.price_cents / p.usual_cents) * 100)}% below usual")
    elif p.change_cents:
        bits.append(f"{signed(p.change_cents)} since last check")
    if p.detail:
        bits.append(p.detail)
    return " · ".join(bits)


def _headline(s: ShowSummary) -> str:
    if not s.perfs:
        if s.status is None:
            return "not checked yet"
        return {"sold_out": "sold out", "no_inventory": "nothing on sale"}.get(s.status, "couldn't check")
    best = s.cheapest[0]
    line = f"from {money(best.price_cents)} ({when(best.performance)})"
    if s.low_change_cents:
        line += f" · {signed(s.low_change_cents)} vs previous day"
    return line


def _problem(s: ShowSummary, owner: bool) -> str | None:
    if s.status == Status.SCRAPE_FAILED.value:
        old = f" Showing prices from {when(s.last_good_at)}." if s.last_good_at else ""
        return (f"Couldn't check today: {s.reason}." if owner else "Couldn't check today.") + old
    if owner and s.status == Status.AVAILABLE.value and s.reason:
        return s.reason  # partial (e.g. some Met nights failed)
    return None


@dataclass
class Audience:
    owner: bool                 # the owner's copy has maintenance notes
    link: str | None            # base URL of the page with calendars & history, if any
    footer: str


def owner_audience() -> Audience:
    return Audience(True, WEB_URL, f"Full calendars and price history: {WEB_URL} (start it with scripts/page.sh).")


def friend_audience(public_url: str | None) -> Audience:
    more = f" Full calendars and price history: {public_url}" if public_url else ""
    return Audience(False, public_url.rstrip("/") + "/" if public_url else None,
                    "You're on this list by request. It covers the shows I'm tracking, checked every morning; "
                    "prices are before fees." + more + " Reply to this email to stop getting it.")


def _attention(summaries: list[ShowSummary], ended: list[dict]) -> list[str]:
    out = [f"{s.name} hasn't been checked successfully for {s.fail_days} days ({s.reason}). "
           "The site may have changed or be blocking the tracker." for s in summaries if s.fail_days >= FAIL_WARN_DAYS]
    out += [f"Stopped checking {e['name']}: it has no upcoming performances." for e in ended]
    return out


def _show_link(a: Audience, s: ShowSummary) -> str | None:
    if not a.link:
        return None
    return f"{a.link.rstrip('/')}/#show-{s.id}" if a.owner else f"{a.link}#show-{s.id}"


def subject(summaries: list[ShowSummary], today: str) -> str:
    deals = sum(1 for s in summaries for p in s.cheapest[:TOP_N] if p.deal)
    day = datetime.fromisoformat(today).strftime("%a %b %-d")
    lows = ", ".join(f"{s.name} {money(s.cheapest[0].price_cents)}" for s in summaries if s.perfs)
    head = f"Show prices {day}" + (f" — {deals} deal{'s' * (deals != 1)}" if deals else "")
    return (head + ": " + lows)[:180] if lows else head


def text(summaries: list[ShowSummary], audience: Audience | None = None, ended: list[dict] = ()) -> str:
    a = audience or owner_audience()
    out = []
    if a.owner and (notes := _attention(summaries, list(ended))):
        out += ["NEEDS ATTENTION"] + [f"  ! {n}" for n in notes] + [""]
    for s in summaries:
        out.append(f"{s.name} — {_headline(s)}")
        if (p := _problem(s, a.owner)):
            out.append(f"  ! {p}")
        for perf in s.cheapest[:TOP_N]:
            usual = f"  (usually {money(perf.usual_cents)})" if perf.usual_cents is not None else ""
            n = note(perf)
            out.append(f"  {when(perf.performance):<24} {money(perf.price_cents):>8}{usual}" + (f"  {n}" if n else ""))
        out.append(f"  Buy: {s.url}")
        out.append("")
    out.append(a.footer)
    return "\n".join(out)


def html(summaries: list[ShowSummary], audience: Audience | None = None, ended: list[dict] = ()) -> str:
    a = audience or owner_audience()
    css_cell = "padding:4px 12px 4px 0;border-bottom:1px solid #e6e5e0;font-size:14px;"
    parts = ['<div style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#0b0b0b;max-width:640px">']
    if a.owner and (notes := _attention(summaries, list(ended))):
        parts.append('<div style="background:#fbeaea;color:#a3332f;border-radius:8px;padding:10px 12px;font-size:14px">'
                     '<b>Needs attention</b>' + "".join(f"<div>⚠ {escape(n)}</div>" for n in notes) + "</div>")
    for s in summaries:
        link = _show_link(a, s)
        name = (f'<a href="{escape(link)}" style="color:#0b0b0b;text-decoration:none">{escape(s.name)}</a>'
                if link else escape(s.name))
        parts.append(f'<h3 style="margin:20px 0 2px;font-size:17px">{name}</h3>')
        parts.append(f'<div style="color:#52514e;font-size:14px;margin-bottom:6px">{escape(_headline(s))}</div>')
        if (p := _problem(s, a.owner)):
            parts.append(f'<div style="color:#a3332f;font-size:13px;margin-bottom:6px">⚠ {escape(p)}</div>')
        if s.perfs:
            parts.append('<table style="border-collapse:collapse">')
            parts.append(f'<tr style="color:#52514e;font-size:12px;text-align:left"><th style="{css_cell}">Date</th>'
                         f'<th style="{css_cell}text-align:right">Price</th><th style="{css_cell}text-align:right">Usually</th>'
                         f'<th style="{css_cell}"></th></tr>')
            for perf in s.cheapest[:TOP_N]:
                tag = (f'<span style="background:#e3f4e3;color:#0a6b0a;border-radius:4px;padding:1px 6px;font-weight:600">'
                       f'{escape(note(perf))}</span>') if perf.deal else f'<span style="color:#52514e">{escape(note(perf))}</span>'
                parts.append(f'<tr><td style="{css_cell}">{escape(when(perf.performance))}</td>'
                             f'<td style="{css_cell}text-align:right;font-weight:600">{money(perf.price_cents)}</td>'
                             f'<td style="{css_cell}text-align:right;color:#52514e">{money(perf.usual_cents)}</td>'
                             f'<td style="{css_cell}">{tag}</td></tr>')
            parts.append("</table>")
        parts.append(f'<div style="font-size:12px;margin-top:4px"><a href="{escape(s.url)}" style="color:#2a78d6">Buy on {escape(s.site)}</a></div>')
    parts.append(f'<p style="margin-top:24px;font-size:13px;color:#52514e">{_linkify(a.footer)}</p></div>')
    return "".join(parts)


def _linkify(t: str) -> str:
    return re.sub(r"https?://[^\s)]+[^\s).,]", lambda m: f'<a href="{m.group(0)}" style="color:#2a78d6">{m.group(0)}</a>',
                  escape(t))

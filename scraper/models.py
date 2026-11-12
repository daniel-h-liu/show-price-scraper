"""Shapes every site adapter reports in (see docs/design.md).

Times are naive New York local ISO strings ('2026-11-16T20:00'). Every show is in NYC, so this keeps
storage, comparison, and display trivial."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from zoneinfo import ZoneInfo

NYC = ZoneInfo("America/New_York")


class Status(str, Enum):
    AVAILABLE = "available"
    SOLD_OUT = "sold_out"            # performances exist, nothing left to buy
    NO_INVENTORY = "no_inventory"    # nothing listed at all
    SCRAPE_FAILED = "scrape_failed"  # blocked / timeout / unrecognised page -- never a price


def now_local() -> str:
    return datetime.now(NYC).replace(tzinfo=None).isoformat(timespec="seconds")


def to_local(iso: str) -> str:
    """Any ISO datetime with offset/Z -> naive NYC local 'YYYY-MM-DDTHH:MM'."""
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if dt.tzinfo is not None:
        dt = dt.astimezone(NYC).replace(tzinfo=None)
    return dt.isoformat(timespec="minutes")


@dataclass(frozen=True)
class Offer:
    """The cheapest purchasable option an adapter found for one performance (or one seat zone)."""
    price_cents: int
    performance: str | None = None  # naive NYC local ISO
    detail: str | None = None


@dataclass
class ParseResult:
    """What a site's parser found: purchasable offers plus how many performances/seats it saw at all."""
    offers: list[Offer] = field(default_factory=list)
    seen: int = 0
    note: str | None = None  # partial problems worth surfacing (e.g. one Met night failed to load)


@dataclass(frozen=True)
class CheckResult:
    """One check of one show: every purchasable performance (cheapest offer each), or why there are none."""
    status: Status
    performances: tuple[Offer, ...] = ()
    reason: str | None = None
    seen: int = 0
    checked_at: str = field(default_factory=now_local)

    def __post_init__(self):
        object.__setattr__(self, "status", Status(self.status))
        object.__setattr__(self, "performances", tuple(self.performances))
        if self.status is Status.AVAILABLE and not self.performances:
            raise ValueError("an available check needs at least one priced performance")
        if self.status is not Status.AVAILABLE and self.performances:
            raise ValueError(f"a {self.status.value} check must not carry prices")
        for o in self.performances:
            if not isinstance(o.price_cents, int) or o.price_cents < 0:
                raise ValueError(f"bad price {o.price_cents!r}")
        if self.status is Status.SCRAPE_FAILED and not self.reason:
            raise ValueError("scrape_failed needs a reason")

    @property
    def cheapest(self) -> Offer | None:
        return min(self.performances, key=lambda o: (o.price_cents, o.performance or "")) if self.performances else None


def failed(reason: str) -> CheckResult:
    return CheckResult(Status.SCRAPE_FAILED, reason=reason)


def build_result(r: ParseResult, date_note: str = "") -> CheckResult:
    """Collapse offers to the cheapest per performance and pick a status."""
    best: dict[str | None, Offer] = {}
    for o in r.offers:
        cur = best.get(o.performance)
        if cur is None or o.price_cents < cur.price_cents:
            best[o.performance] = o
    perfs = sorted(best.values(), key=lambda o: o.performance or "")
    if perfs:
        return CheckResult(Status.AVAILABLE, perfs, reason=r.note, seen=r.seen)
    if r.seen:
        return CheckResult(Status.SOLD_OUT, reason=r.note or f"{r.seen} listed, none purchasable", seen=r.seen)
    return CheckResult(Status.NO_INVENTORY, reason=r.note or "nothing listed")

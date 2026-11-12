"""Site parsers against trimmed real responses captured Nov 11, 2026."""
import copy

from scraper.catalog import parse_category
from scraper.models import Status, build_result
from scraper.sites import KNOWN_BLOCKED, adapter_for, atg, check_supported, domain, metopera, todaytix


# ---- TodayTix (Galileo, 6 performances: $302, $72 x3, $62, $69) ----

def test_todaytix_every_performance_in_local_time(fixture):
    r = build_result(todaytix.parse(fixture("todaytix_occurrences.json")))
    assert r.status is Status.AVAILABLE
    assert [p.performance for p in r.performances][:2] == ["2026-11-11T20:00", "2026-11-12T20:00"]
    assert len(r.performances) == 6
    assert r.cheapest.price_cents == 6200 and r.cheapest.performance == "2026-11-16T20:00"
    assert r.performances[0].detail == "1 left"


def test_todaytix_zero_quantity_everywhere_is_sold_out(fixture):
    body = copy.deepcopy(fixture("todaytix_occurrences.json"))
    for o in body["data"]:
        o["quantityAvailable"] = 0
    r = build_result(todaytix.parse(body))
    assert r.status is Status.SOLD_OUT and r.performances == ()


def test_todaytix_url_matching():
    url = "https://www.todaytix.com/nyc/shows/46289-galileo-on-broadway"
    assert adapter_for(url) is todaytix
    assert todaytix.is_data_response("https://api.todaytix.com/api/v3/events/46289/occurrences?limit=1000", url)
    assert not todaytix.is_data_response("https://api.todaytix.com/api/v3/events/999/occurrences", url)


# ---- ATG (Hadestown: LOW 139/99/119, MEDIUM 119 x3, GOOD 59/79/69) ----

def test_atg_utc_dates_become_new_york_evenings(fixture):
    r = build_result(atg.parse(fixture("atg_calendar.json")))
    assert r.cheapest.price_cents == 5900
    assert r.cheapest.performance == "2026-11-19T19:00"  # 2026-11-20T00:00Z
    assert r.performances[0].performance == "2026-11-11T19:30" and r.performances[0].detail == "few left"


def test_atg_unknown_status_not_counted_as_available(fixture):
    body = copy.deepcopy(fixture("atg_calendar.json"))
    for p in atg.performances(body):
        p["availabilityStatus"] = "SOLD_OUT"
    assert build_result(atg.parse(body)).status is Status.SOLD_OUT


def test_atg_ignores_other_graphql_responses(fixture):
    other = {"data": {"createSession": True}}
    r = atg.parse_captured([other, fixture("atg_calendar.json"), other])
    assert r is not None and r.seen == 9
    assert atg.parse_captured([other]) is None


# ---- Met Opera (Orchestra sample: Rear $95 available, Premium Aisle $315 available, Prime unavailable) ----

def test_met_cheapest_zone_per_performance(fixture):
    r = build_result(metopera.parse_performance([fixture("met_seatmap_orchestra.json")], "2026-11-27T19:30"))
    assert len(r.performances) == 1  # zones collapse to the cheapest per performance
    assert r.cheapest.price_cents == 9500 and r.cheapest.detail == "Orchestra Rear"


def test_met_combines_levels_and_dedupes_repeats(fixture):
    orch = fixture("met_seatmap_orchestra.json")
    fam = copy.deepcopy(orch)
    fam["levelId"] = "family"
    for z in fam["allSeatPricing"]:
        for p in z["prices"]:
            p["price"] = 23.0
    r = metopera.parse_performance([orch, orch, fam], "2026-11-27T19:30")
    assert r.seen == 2 * len(orch["levelSeats"])
    assert build_result(r).cheapest.price_cents == 2300


def test_met_skips_score_desks_but_keeps_standing_room(fixture):
    orch = fixture("met_seatmap_orchestra.json")
    def level(level_id, zone_name, price):
        b = copy.deepcopy(orch)
        b["levelId"] = level_id
        for s in b["levelSeats"]:
            s["zoneDescription"] = zone_name
        for z in b["allSeatPricing"]:
            z["description"] = zone_name
            for p in z["prices"]:
                p["price"] = price
        return b
    r = build_result(metopera.parse_performance([orch, level("fc", "Score Desks", 23.0)], "x"))
    assert r.cheapest.price_cents == 9500
    r = build_result(metopera.parse_performance([orch, level("sr", "Standing Room", 30.0)], "x"))
    assert r.cheapest.price_cents == 3000 and r.cheapest.detail.endswith("Standing Room")


def test_met_all_unavailable_is_sold_out(fixture):
    body = copy.deepcopy(fixture("met_seatmap_orchestra.json"))
    for s in body["levelSeats"]:
        s["tessituraSeat"]["isAvailable"] = False
    assert build_result(metopera.parse_performance([body], "x")).status is Status.SOLD_OUT


def test_met_performance_list_from_escaped_page_json():
    html = ('..."productionPerformances\\":[{\\"id\\":19152,\\"title\\":\\"Samson et Dalila\\",'
            '\\"date\\":\\"2026-11-27T19:30:00-05:00\\",\\"dateString\\":\\"Nov 27 2026\\"},'
            '{\\"id\\":19156,\\"title\\":\\"Samson et Dalila\\",\\"date\\":\\"2026-12-05T12:00:00-05:00\\"}]...'
            '{\\"id\\":19152,\\"title\\":\\"Samson et Dalila\\",\\"date\\":\\"2026-11-27T19:30:00-05:00\\"}')
    assert metopera.performances_from_html(html) == [
        (19152, "Samson et Dalila", "2026-11-27T19:30"), (19156, "Samson et Dalila", "2026-12-05T12:00")]


def test_met_url_matching():
    assert adapter_for("https://www.metopera.org/smartseat?itemNumber=19152#/") is metopera
    assert adapter_for("https://www.metopera.org/season/") is None


# ---- registry / catalog ----

def test_supported_urls():
    assert domain("https://www.telecharge.com/A-Few-Good-Men-Tickets?AID=1") in KNOWN_BLOCKED
    assert "TodayTix" in check_supported("https://tickets.broadwaydirect.com/shop/tickets/series/1")
    assert "isn't supported" in check_supported("https://2st.com/shows/the-fantasticks")
    assert check_supported("https://www.todaytix.com/nyc/shows/1-x") is None


def test_catalog_rows(fixture):
    rows, total = parse_category(fixture("todaytix_category.json"), "Broadway", "2026-11-11T08:00")
    r = rows[0]
    assert r["url"] == "https://www.todaytix.com/nyc/shows/46971-paranormal-activity"
    assert r["name"] == "Paranormal Activity" and r["from_price_cents"] == 5900
    assert r["venue"] == "August Wilson Theatre" and r["image_url"].startswith("https://images.ctfassets.net/")


def test_catalog_builds_slug_when_missing(fixture):
    data = copy.deepcopy(fixture("todaytix_category.json"))
    s = data["props"]["pageProps"]["productList"]["results"]["shows"][0]
    s.update(id=384, slug=None, displayName="Hamilton")
    rows, _ = parse_category(data, "Broadway", "t")
    assert rows[0]["url"] == "https://www.todaytix.com/nyc/shows/384-hamilton"


def test_met_level_ids_from_escaped_page_json():
    html = ('{\\"availableCount\\":270,\\"seatmapId\\":\\"38028bac-b297-4f25-9cb0-7576fb0f418a\\",\\"x\\":1}'
            '{\\"availableCount\\":0,\\"seatmapId\\":\\"9d75a6e0-0686-4d54-bfce-7e935ab6a3dd\\"}'
            '{\\"availableCount\\":5,\\"seatmapId\\":\\"38028bac-b297-4f25-9cb0-7576fb0f418a\\"}')
    assert metopera.level_seatmap_ids(html) == ["38028bac-b297-4f25-9cb0-7576fb0f418a", "9d75a6e0-0686-4d54-bfce-7e935ab6a3dd"]

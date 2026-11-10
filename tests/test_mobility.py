"""Invented places and responses only; no live Google or Keychain calls."""
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from hub import config, household
from hub.main import app
from hub.tools.mobility import google, pages, places, planner

client = TestClient(app)


@pytest.fixture(autouse=True)
def example_city():
    """A fictional city: the real one lives only in the private household file."""
    path = config.DATA_DIR / household.FILE
    data = json.loads(path.read_text()) if path.exists() else {}
    data["mobility"] = {"city": "Example City", "timezone": "America/Los_Angeles",
                        "bounds": {"south": 39.5, "north": 40.5, "west": -100.5, "east": -99.5},
                        "anchors": [["Invented Ave & 1st St", 40.09, -100.04], ["Invented Ave & 9th St", 40.02, -100.03]],
                        "default_destination": "Invented Plaza"}
    path.write_text(json.dumps(data))


def location(lat=40.08, lon=-100.05):
    return {"latLng": {"latitude": lat, "longitude": lon}}


def route(departure, vehicle="BUS"):
    start, end = location(), location(40.01, -100.03)
    return {"duration": "1200s", "distanceMeters": 8000, "legs": [{"startLocation": start, "endLocation": end, "steps": [
        {"travelMode": "TRANSIT", "transitDetails": {
            "stopDetails": {"departureTime": departure.isoformat(), "arrivalTime": (departure + timedelta(minutes=15)).isoformat(),
                            "departureStop": {"name": "Invented North Stop", "location": start},
                            "arrivalStop": {"name": "Invented South Stop", "location": end}},
            "transitLine": {"nameShort": "99", "vehicle": {"type": vehicle}}, "headsign": "Invented Center"}},
        {"travelMode": "WALK", "staticDuration": "300s", "startLocation": end, "endLocation": end},
    ]}]}


class FakeRoutes:
    def __init__(self):
        self.calls = 0
        self.requests = []
        self.vehicle = "BUS"
        self.late = False

    def compute(self, origin, destination, mode, departure=None, alternatives=False):
        self.calls += 1
        self.requests.append((origin, destination, mode, departure))
        if mode == "BICYCLE":
            return [{"duration": "180s" if "address" in destination else "300s", "distanceMeters": 1000, "warnings": ["Invented cycling warning"]}]
        if mode == "WALK":
            return [{"duration": "300s" if "address" in destination else "600s", "distanceMeters": 1000}]
        return [route(departure + timedelta(minutes=-1 if self.late else 10), self.vehicle)]


@pytest.fixture(autouse=True)
def isolate_maps(monkeypatch):
    monkeypatch.setattr(google, "key", lambda: pytest.fail("Offline tests must not access Keychain"))
    pages._searches.clear()


def test_bike_plan_uses_actual_access_then_retimes_bus():
    api = FakeRoutes()
    result = planner.search("Invented North Library", "Invented South Library", api=api)
    assert len(result["routes"]) == 1
    option = result["routes"][0]
    assert option["minutes"] == 33
    assert option["ride_minutes"] == 8
    assert option["buses"] == 1
    assert [s["kind"] for s in option["steps"]] == ["ride", "wait", "bus", "ride"]
    baseline_time = api.requests[0][3]
    retimed = next(r for r in api.requests if r[2] == "TRANSIT" and "location" in r[0])
    assert retimed[3] == baseline_time + timedelta(minutes=5)
    assert result["calls"] <= google.MAX_CALLS


def test_lime_keeps_last_mile_walk_without_added_handling_time():
    result = planner.search("Invented Start", "Invented End", "lime", api=FakeRoutes())
    option = result["routes"][0]
    assert option["minutes"] == 35
    assert [s["kind"] for s in option["steps"]] == ["ride", "wait", "bus", "walk"]
    assert "proxy" in option["steps"][0]["note"]


@pytest.mark.parametrize("vehicle", ["SUBWAY", "TRAM", "FERRY"])
def test_bus_preference_is_enforced_not_just_requested(vehicle):
    api = FakeRoutes()
    api.vehicle = vehicle
    assert planner.search("Invented Start", "Invented End", api=api)["routes"] == []


def test_missed_connection_is_rejected():
    api = FakeRoutes()
    api.late = True
    assert planner.search("Invented Start", "Invented End", api=api)["routes"] == []


def test_changed_boarding_stop_keeps_real_walk():
    class WalkingRoutes(FakeRoutes):
        def compute(self, *args, **kwargs):
            result = super().compute(*args, **kwargs)
            if args[2] == "TRANSIT" and "location" in args[0]:
                result[0]["legs"][0]["steps"].insert(0, {"travelMode": "WALK", "staticDuration": "180s",
                    "startLocation": location(), "endLocation": location(40.081)})
            return result
    result = planner.search("Invented Start", "Invented End", api=WalkingRoutes())
    assert result["routes"][0]["steps"][1]["kind"] == "walk"


def test_actual_transfer_walk_still_rejects_missed_connection():
    api = FakeRoutes()
    seed = route(datetime.now(timezone.utc) + timedelta(hours=1))
    original = api.compute
    def compute(*args, **kwargs):
        result = original(*args, **kwargs)
        if args[2] == "TRANSIT":
            first = result[0]["legs"][0]["steps"][0]
            arrival = planner.timestamp(first["transitDetails"]["stopDetails"]["arrivalTime"])
            second = route(arrival + timedelta(minutes=2))["legs"][0]["steps"][0]
            walk = {"travelMode": "WALK", "staticDuration": "180s", "startLocation": location(), "endLocation": location()}
            result[0]["legs"][0]["steps"] = [first, walk, second]
        return result
    api.compute = compute
    with pytest.raises(planner.CandidateUnavailable):
        planner.combine(api, seed, google.address("Invented Start"), google.address("Invented End"),
                        datetime.now(timezone.utc), "bike", 25)


@pytest.mark.parametrize("value", ["-2s", "nans", "infs", "90000s", None, "10"])
def test_invalid_provider_duration_is_not_used(value):
    with pytest.raises(planner.CandidateUnavailable):
        planner.seconds(value)


@pytest.mark.parametrize("value", ["2026-11-01T01:30", "2027-03-14T02:30", "nonsense", "2030-01-01T12:00"])
def test_departure_validation(value):
    with pytest.raises(google.MapsError):
        planner.parse_departure(value, datetime(2026, 10, 31, tzinfo=timezone.utc))


def test_departure_uses_configured_time_zone():
    now = datetime(2026, 11, 4, 16, tzinfo=timezone.utc)
    assert planner.parse_departure("2026-11-04T09:00", now).hour == 17
    assert planner.parse_departure("", now) == now


def test_addresses_and_coordinates_are_bounded():
    with pytest.raises(google.MapsError):
        google.address("x" * 241)
    with pytest.raises(google.MapsError):
        google.address("Bad\naddress")
    with pytest.raises(google.MapsError):
        google.point(location(40, -120))


def transport_api(monkeypatch, handler):
    monkeypatch.setattr(google, "key", lambda: "synthetic-key-not-a-real-secret")
    real_client = httpx.Client
    monkeypatch.setattr(google.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    return google.Routes()


def test_google_transport_is_fixed_bounded_and_no_redirects(monkeypatch):
    def handler(request):
        assert str(request.url) == google.URL
        assert request.method == "POST"
        assert request.headers["X-Goog-Api-Key"] == "synthetic-key-not-a-real-secret"
        body = json.loads(request.content)
        assert body["transitPreferences"] == {"allowedTravelModes": ["BUS"]}
        # Live Google can return more than the documented number of alternatives.
        return httpx.Response(200, json={"routes": [{}] * 6})
    api = transport_api(monkeypatch, handler)
    try:
        assert len(api.compute(google.address("Fake Start"), google.address("Fake End"), "TRANSIT", datetime.now(timezone.utc))) == 4
        api.calls = google.MAX_CALLS
        with pytest.raises(google.MapsError, match="limit"):
            api.compute({}, {}, "BICYCLE")
    finally:
        api.close()
    assert api.token is None


@pytest.mark.parametrize("status", [302, 400, 403, 429, 500])
def test_google_errors_never_echo_payload_or_follow_redirect(monkeypatch, status):
    api = transport_api(monkeypatch, lambda request: httpx.Response(status, headers={"Location": "https://example.invalid"},
        json={"error": {"message": "PRIVATE_PROVIDER_RESPONSE"}}))
    try:
        with pytest.raises(google.MapsError) as exc:
            api.compute({}, {}, "BICYCLE")
        assert "PRIVATE_PROVIDER_RESPONSE" not in str(exc.value)
        assert api.calls == 1
    finally:
        api.close()


def test_oversized_response_rejected(monkeypatch):
    api = transport_api(monkeypatch, lambda request: httpx.Response(200, content=b"x" * (google.MAX_BYTES + 1)))
    try:
        with pytest.raises(google.MapsError, match="limits"):
            api.compute({}, {}, "BICYCLE")
    finally:
        api.close()


def test_ui_search_auth_origin_privacy_and_escaping(monkeypatch):
    monkeypatch.setattr(pages.planner, "search", lambda *args, **kwargs: {"routes": [], "calls": 3, "checked": 1, "checked_at": "9:00 AM"})
    assert client.get("/mobility").status_code == 200
    assert client.get("/mobility", headers={"Tailscale-User-Login": "other@example.com"}).status_code == 403
    form = {"origin": '<script>alert("invented")</script>', "destination": "Invented Destination"}
    assert client.post("/mobility/search", data=form).status_code == 403
    response = client.post("/mobility/search", data=form, headers={"Origin": "http://testserver"})
    assert response.status_code == 200
    assert "&lt;script&gt;" in response.text
    assert form["origin"] not in response.text
    assert "No usable bike + bus" in response.text
    assert response.headers["cache-control"] == "no-store"
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert "origin=" not in str(response.url)
    assert client.get("/mobility/search").status_code == 405


def test_ui_safety_limits_and_error(monkeypatch):
    monkeypatch.setattr(pages.planner, "search", lambda *args, **kwargs: (_ for _ in ()).throw(google.MapsError("Synthetic Maps failure")))
    for _ in range(6):
        response = client.post("/mobility/search", data={"origin": "Fake Origin", "destination": "Fake Destination"}, headers={"Origin": "http://testserver"})
        assert "Synthetic Maps failure" in response.text
    response = client.post("/mobility/search", data={"origin": "Fake Origin", "destination": "Fake Destination"}, headers={"Origin": "http://testserver"})
    assert "hourly safety limit" in response.text


def test_real_planner_fake_google_renders_end_to_end(monkeypatch):
    monkeypatch.setattr(planner, "Routes", FakeRoutes)
    monkeypatch.setattr(FakeRoutes, "close", lambda self: None, raising=False)
    response = client.post("/mobility/search", data={"origin": "Invented North Library", "destination": "Invented South Library"}, headers={"Origin": "http://testserver"})
    assert response.status_code == 200
    assert 'data-minutes="33"' in response.text
    assert "Invented North Stop" in response.text
    assert "Google Maps" in response.text
    assert "Unload bike" not in response.text
    assert 'name="buffer"' not in response.text


def test_unavailable_key_fails_without_provider_call(monkeypatch):
    monkeypatch.setattr(google, "key", lambda: None)
    with pytest.raises(google.MapsError, match="key unavailable"):
        google.Routes()


def test_long_riding_leg_excluded():
    class LongRoutes(FakeRoutes):
        def compute(self, *args, **kwargs):
            result = super().compute(*args, **kwargs)
            if args[2] == "BICYCLE":
                result[0]["duration"] = "3600s"
            return result
    assert planner.search("Invented Start", "Invented End", api=LongRoutes())["routes"] == []


def test_parallel_search_does_not_make_provider_call(monkeypatch):
    monkeypatch.setattr(planner, "search", lambda *args: pytest.fail("No simultaneous paid searches"))
    pages._lock.acquire()
    try:
        response = client.post("/mobility/search", data={"origin": "Fake Origin", "destination": "Fake Destination"}, headers={"Origin": "http://testserver"})
        assert "Another route search" in response.text
    finally:
        pages._lock.release()


def test_malformed_routes_fail_closed(monkeypatch):
    api = transport_api(monkeypatch, lambda request: httpx.Response(200, json={"routes": [None]}))
    try:
        with pytest.raises(google.MapsError, match="usable response"):
            api.compute({}, {}, "BICYCLE")
    finally:
        api.close()


@pytest.mark.parametrize("kind,expected_rides", [("bike", 2), ("lime", 1), ("lime_last", 1), ("lime_both", 2)])
def test_first_and_last_mile_modes(kind, expected_rides):
    result = planner.search("Invented North", "Invented South", kind, api=FakeRoutes())
    option = result["routes"][0]
    assert len([s for s in option["steps"] if s["kind"] == "ride"]) == expected_rides
    if kind == "lime_last":
        assert not any("boarding area" in s["title"] for s in option["steps"])
    if kind in ("lime_last", "lime_both"):
        assert option["steps"][-1]["title"] == "Scooter to destination (estimate)"
    assert option["buffer_minutes"] == 0
    assert not any(s["kind"] == "buffer" for s in option["steps"])
    assert all("start" in s and "finish" in s for s in option["steps"])
    assert sum(s["seconds"] for s in option["steps"]) / 60 == option["minutes"]


def test_stale_form_buffer_cannot_add_time(monkeypatch):
    monkeypatch.setattr(planner, "Routes", FakeRoutes)
    monkeypatch.setattr(FakeRoutes, "close", lambda self: None, raising=False)
    response = client.post("/mobility/search", data={"origin": "Invented North", "destination": "Invented South", "buffer": 8}, headers={"Origin": "http://testserver"})
    assert 'data-minutes="33"' in response.text
    assert "Transfer buffer" not in response.text


def test_comparisons_use_identical_buses_not_the_faster_baseline():
    result = planner.search("Invented North", "Invented South", api=FakeRoutes())
    assert "comparisons" not in result
    option = result["routes"][0]
    comparison = option["walking_comparison"]
    assert comparison["signature"] == option["signature"]
    assert comparison["state"] == "same_departure"
    assert comparison["minutes"] == 35
    assert option["vs_bus"] == 2


def test_search_cache_deduplicates_without_persisting():
    api = FakeRoutes()
    cached = planner.SearchCache(api)
    for _ in range(3):
        cached.compute(google.address("Invented North"), google.address("Invented South"), "BICYCLE")
    assert cached.calls == 1
    fresh = planner.SearchCache(api)
    fresh.compute(google.address("Invented North"), google.address("Invented South"), "BICYCLE")
    assert fresh.calls == 2


def test_all_retimed_alternatives_are_checked_not_only_first():
    class Alternatives(FakeRoutes):
        def compute(self, *args, **kwargs):
            result = super().compute(*args, **kwargs)
            if args[2] == "TRANSIT" and "location" in args[0]:
                slow = route(args[3] + timedelta(minutes=40))
                return [slow, result[0]]
            return result
    result = planner.search("Invented North", "Invented South", api=Alternatives())
    assert result["routes"][0]["minutes"] == 33


def test_nearly_identical_options_collapse_but_tradeoffs_remain():
    import copy
    a = planner.search("Invented North", "Invented South", api=FakeRoutes())["routes"][0]
    b, c = copy.deepcopy(a), copy.deepcopy(a)
    b.update(signature=("different stop",), minutes=a["minutes"] + 1, end=a["end"] + timedelta(minutes=1))
    c.update(signature=("less riding",), minutes=a["minutes"] + 8, end=a["end"] + timedelta(minutes=8), ride_minutes=1)
    selected = planner.distinct([c, b, a])
    assert len(selected) == 2
    assert selected[0]["badges"] == ["Earliest arrival"]
    assert selected[1]["badges"] == ["Less riding"]


def test_limits_keep_previous_successful_options():
    class Limited(FakeRoutes):
        def compute(self, *args, **kwargs):
            if args[2] == "TRANSIT" and "location" in args[0]:
                if getattr(self, "already_retimed", False):
                    raise google.SearchLimit("Synthetic limit")
                self.already_retimed = True
            result = super().compute(*args, **kwargs)
            if args[2] == "TRANSIT" and "address" in args[0] and "1st St" in args[0]["address"]:
                result[0]["legs"][0]["steps"][0]["transitDetails"]["stopDetails"]["departureStop"]["location"] = location(40.09)
            return result
    result = planner.search("Invented North", "Invented South", api=Limited())
    assert result["limited"]
    assert result["routes"]


def test_timeline_crossing_midnight_has_next_day_label():
    departure = datetime(2026, 11, 5, 7, 55, tzinfo=timezone.utc)
    itinerary = []
    end = planner.segment(itinerary, departure, "ride", "Invented ride", 600)
    result = planner.finish(itinerary, departure, end)
    assert result["next_day"]
    assert result["arrival"] == "12:05 AM"


def test_shortcut_is_explicit_owner_only_and_forgettable(monkeypatch):
    monkeypatch.setattr(planner, "search", lambda *args, **kwargs: {"routes": [], "calls": 1, "checked": 0, "checked_at": "9:00 AM"})
    form = {"origin": "Invented North Library", "destination": "Invented South Library"}
    client.post("/mobility/search", data=form, headers={"Origin": "http://testserver"})
    assert places.load() is None
    response = client.post("/mobility/search", data={**form, "remember": "yes"}, headers={"Origin": "http://testserver"})
    assert "Trip shortcut saved" in response.text
    assert places.load() == form
    assert places.path().stat().st_mode & 0o777 == 0o600
    assert "Invented North Library" in client.get("/mobility").text
    assert client.post("/mobility/forget").status_code == 403
    response = client.post("/mobility/forget", headers={"Origin": "http://testserver"}, follow_redirects=False)
    assert response.status_code == 303
    assert places.load() is None


def test_shortcut_refuses_insecure_files_or_symlinks(tmp_path):
    places.save("Invented North", "Invented South")
    places.path().chmod(0o644)
    assert places.load() is None
    places.path().unlink()
    target = tmp_path / "invented-private.json"
    target.write_text('{"origin":"Invented North","destination":"Invented South"}')
    target.chmod(0o600)
    places.path().symlink_to(target)
    assert places.load() is None
    places.forget()
    assert target.is_file()


def test_invalid_form_does_not_consume_paid_slot(monkeypatch):
    monkeypatch.setattr(planner, "search", lambda *args, **kwargs: pytest.fail("Invalid form must not search"))
    response = client.post("/mobility/search", data={"origin": "Invented Place", "destination": "Invented Place"}, headers={"Origin": "http://testserver"})
    assert "two different places" in response.text
    assert len(pages._searches) == 0


@pytest.mark.parametrize("kind", ["bike", "lime", "lime_last", "lime_both"])
def test_all_modes_render_with_comparisons_and_timeline(monkeypatch, kind):
    monkeypatch.setattr(planner, "Routes", FakeRoutes)
    monkeypatch.setattr(FakeRoutes, "close", lambda self: None, raising=False)
    response = client.post("/mobility/search", data={"origin": "Invented North", "destination": "Invented South", "kind": kind}, headers={"Origin": "http://testserver"})
    assert response.status_code == 200
    assert "Step-by-step" in response.text
    assert "Same buses + walking" in response.text
    assert "Bike all the way" not in response.text
    assert "timeline-content" in response.text
    assert "Same starting time, stops, scheduled buses and transfers" in response.text
    if kind != "bike":
        assert "Lime is an estimate" in response.text


def test_comparison_makes_only_walking_requests_not_new_transit_search():
    api = FakeRoutes()
    departure = datetime.now(timezone.utc)
    origin, destination = google.address("Invented North"), google.address("Invented South")
    option = planner.combine(api, route(departure + timedelta(hours=1)), origin, destination, departure, "bike", 25)
    before = len(api.requests)
    comparison = planner.same_route_walking(api, option, origin, destination, departure)
    assert [r[2] for r in api.requests[before:]] == ["WALK", "WALK"]
    assert comparison["signature"] == option["signature"]
    assert comparison["lines"] == option["lines"]


def test_walking_that_misses_bus_requires_earlier_departure_not_other_bus():
    class SlowWalking(FakeRoutes):
        def compute(self, *args, **kwargs):
            result = super().compute(*args, **kwargs)
            if args[2] == "WALK" and "location" in args[1]:
                result[0]["duration"] = "1800s"
            return result
    result = planner.search("Invented North", "Invented South", api=SlowWalking())
    option = result["routes"][0]
    comparison = option["walking_comparison"]
    assert comparison["state"] == "leave_earlier"
    assert comparison["earlier_minutes"] == 15
    assert comparison["signature"] == option["signature"]
    assert option["vs_bus"] is None


def test_comparison_retains_exact_transfer_walk_and_departures():
    departure = datetime.now(timezone.utc)
    first = route(departure + timedelta(minutes=15))["legs"][0]["steps"][0]
    second = route(departure + timedelta(minutes=32))["legs"][0]["steps"][0]
    second["transitDetails"]["transitLine"]["nameShort"] = "98"
    walk = {"travelMode": "WALK", "staticDuration": "120s", "startLocation": location(), "endLocation": location()}
    exact = {"legs": [{"steps": [first, walk, second]}]}
    timeline = []
    cursor, _ = planner.transit_segments(exact, timeline, departure, "bike", False)
    option = planner.finish(timeline, departure, cursor)
    option["_transit_route"] = exact
    comparison = planner.same_route_walking(FakeRoutes(), option, google.address("Invented North"), google.address("Invented South"), departure)
    assert comparison["signature"] == option["signature"]
    assert comparison["lines"] == ["99", "98"]
    assert comparison["buffer_minutes"] == 0
    walks = [s for s in comparison["steps"] if s["kind"] == "walk"]
    assert [s["seconds"] for s in walks] == [600, 120, 300]


@pytest.mark.parametrize("reason", ["limit", "unavailable"])
def test_missing_exact_comparison_never_falls_back_to_other_route(reason):
    class MissingWalking(FakeRoutes):
        def compute(self, *args, **kwargs):
            if args[2] == "WALK":
                if reason == "limit":
                    raise google.SearchLimit("Synthetic limit")
                return []
            return super().compute(*args, **kwargs)
    result = planner.search("Invented North", "Invented South", api=MissingWalking())
    option = result["routes"][0]
    assert option["walking_comparison"] == {"state": reason}
    assert option["vs_bus"] is None
    assert "comparisons" not in result
    assert "_transit_route" not in option


def test_walking_miss_is_explained_without_quicker_slower_claim(monkeypatch):
    class SlowWalking(FakeRoutes):
        def compute(self, *args, **kwargs):
            result = super().compute(*args, **kwargs)
            if args[2] == "WALK" and "location" in args[1]:
                result[0]["duration"] = "1800s"
            return result
    monkeypatch.setattr(planner, "Routes", SlowWalking)
    monkeypatch.setattr(SlowWalking, "close", lambda self: None, raising=False)
    response = client.post("/mobility/search", data={"origin": "Invented North", "destination": "Invented South"}, headers={"Origin": "http://testserver"})
    assert "Walking cannot catch" in response.text
    assert "15 min earlier" in response.text
    assert "No different bus route substituted" in response.text
    assert "gets you there" not in response.text

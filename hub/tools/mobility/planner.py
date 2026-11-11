"""Bounded, schedule-checked first/last-mile combinations with honest comparisons."""
import json
import math
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from hub import household

from .google import MAX_CALLS, MapsError, Routes, SearchLimit, address, point

def area():
    """The owner's city settings, read from the private household file (never from Git)."""
    return household.section("mobility")


def zone():
    return ZoneInfo(area().get("timezone", "UTC"))


def anchors():
    """Corridor points near home and work, used to find bus connections; private, so not in Git."""
    return tuple((str(label), float(lat), float(lon)) for label, lat, lon in area().get("anchors", []))


class CandidateUnavailable(Exception):
    pass


def seconds(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d+(?:\.\d+)?s", value):
        raise CandidateUnavailable
    result = float(value[:-1])
    if not math.isfinite(result) or result > 86400:
        raise CandidateUnavailable
    return result


def timestamp(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise CandidateUnavailable
    return result


def clock(value):
    return value.astimezone(zone()).strftime("%-I:%M %p")


def parse_departure(value, now=None):
    now = now or datetime.now(timezone.utc)
    if not value:
        return now
    try:
        local = datetime.fromisoformat(value)
        if local.tzinfo is not None:
            raise ValueError
        # Reject both ambiguous fall-back and nonexistent spring-forward times.
        first, second = local.replace(tzinfo=zone(), fold=0), local.replace(tzinfo=zone(), fold=1)
        if first.utcoffset() != second.utcoffset():
            raise ValueError
        result = first.astimezone(timezone.utc)
        if not now <= result <= now + timedelta(days=7):
            raise ValueError
        return result
    except (ValueError, TypeError):
        raise MapsError("Choose an unambiguous local departure time between now and seven days from now.") from None


def steps(route):
    legs = route.get("legs", [])
    if not isinstance(legs, list) or len(legs) != 1 or not isinstance(legs[0], dict):
        raise CandidateUnavailable
    result = legs[0].get("steps", [])
    if not isinstance(result, list) or len(result) > 2000 or any(not isinstance(s, dict) for s in result):
        raise CandidateUnavailable
    return result


def buses(route):
    found = []
    for index, step in enumerate(steps(route)):
        if step.get("travelMode") == "TRANSIT":
            if step.get("transitDetails", {}).get("transitLine", {}).get("vehicle", {}).get("type") != "BUS":
                raise CandidateUnavailable
            found.append((index, step["transitDetails"]))
        elif step.get("travelMode") != "WALK":
            raise CandidateUnavailable
    if not found or len(found) > 3:
        raise CandidateUnavailable
    return found


def stop(details, which):
    return point(details["stopDetails"][which + "Stop"]["location"])


def coordinates(waypoint):
    p = waypoint["location"]["latLng"]
    return f'{p["latitude"]},{p["longitude"]}'


def map_link(origin, destination, mode):
    def text(p):
        return p.get("address") or coordinates(p)
    return "https://www.google.com/maps/dir/?" + urlencode(
        {"api": "1", "origin": text(origin), "destination": text(destination), "travelmode": mode})


def ground_route(api, origin, destination, mode):
    options = api.compute(origin, destination, mode)
    if not options:
        raise CandidateUnavailable
    route = options[0]
    duration = seconds(route["duration"])
    distance = route.get("distanceMeters")
    if not isinstance(distance, (int, float)) or not 0 <= distance <= 15_000:
        raise CandidateUnavailable
    return {"seconds": duration, "distance": distance, "warnings": route.get("warnings", []),
            "link": map_link(origin, destination, "bicycling" if mode == "BICYCLE" else "walking")}


def riding(api, origin, destination):
    return ground_route(api, origin, destination, "BICYCLE")


KINDS = {"bike": "Your bike", "lime": "Lime before the bus", "lime_last": "Lime after the bus", "lime_both": "Lime at both ends"}
INVALID = (CandidateUnavailable, KeyError, IndexError, TypeError, ValueError, AttributeError)


class SearchCache:
    """Deduplicate identical requests in this search only; never persist route data."""
    def __init__(self, api):
        self.api, self.cache = api, {}
        self.limited = False

    @property
    def calls(self):
        return self.api.calls

    def compute(self, origin, destination, mode, departure=None, alternatives=False):
        ident = json.dumps([origin, destination, mode, departure.isoformat() if departure else None, alternatives], sort_keys=True)
        if ident not in self.cache:
            try:
                self.cache[ident] = self.api.compute(origin, destination, mode, departure, alternatives)
            except SearchLimit:
                self.limited = True
                raise
        return self.cache[ident]


def segment(itinerary, cursor, kind, title, duration, **extra):
    end = cursor + timedelta(seconds=duration)
    itinerary.append({"kind": kind, "title": title, "minutes": math.ceil(duration / 60),
                      "seconds": duration, "start": clock(cursor), "finish": clock(end), **extra})
    return end


def ride_segment(itinerary, cursor, ride, title, scooter=False):
    return segment(itinerary, cursor, "ride", title, ride["seconds"],
                   distance=round(ride["distance"] / 1609.344, 1), link=ride["link"],
                   note="Cycling-route proxy; check the Lime app for a legal route." if scooter else "")


def transit_segments(route, itinerary, cursor, kind, replace_tail):
    transit = buses(route)
    last_index, last_bus = transit[-1]
    selected = steps(route)[:last_index + 1] if replace_tail else steps(route)
    for step in selected:
        if step["travelMode"] == "WALK":
            duration = seconds(step.get("staticDuration", ""))
            if duration:
                cursor = segment(itinerary, cursor, "walk", "Walk your bike / transfer" if kind == "bike" else "Walk", duration,
                                 link=map_link(point(step["startLocation"]), point(step["endLocation"]), "walking"))
            continue
        d = step["transitDetails"]
        depart = timestamp(d["stopDetails"]["departureTime"])
        arrive = timestamp(d["stopDetails"]["arrivalTime"])
        if depart < cursor or arrive <= depart:
            raise CandidateUnavailable
        if depart > cursor:
            cursor = segment(itinerary, cursor, "wait", "Wait for bus", (depart - cursor).total_seconds())
        line = d["transitLine"]
        cursor = segment(itinerary, cursor, "bus", "Bus " + line.get("nameShort", line.get("name", "")),
                         (arrive - depart).total_seconds(), line=line.get("nameShort", line.get("name", "Bus")),
                         agency=", ".join(a.get("name", "") for a in line.get("agencies", [])),
                         headsign=d.get("headsign", ""), depart=clock(depart), arrive=clock(arrive),
                         **{"from": d["stopDetails"]["departureStop"]["name"], "to": d["stopDetails"]["arrivalStop"]["name"]},
                         link=map_link(stop(d, "departure"), stop(d, "arrival"), "transit"))
    return cursor, transit


def finish(itinerary, departure, cursor, warnings=()):
    duration = (cursor - departure).total_seconds()
    if not 0 < duration <= 4 * 3600:
        raise CandidateUnavailable
    bus_steps = [s for s in itinerary if s["kind"] == "bus"]
    totals = {kind: math.ceil(sum(s["seconds"] for s in itinerary if s["kind"] == kind) / 60)
              for kind in ("ride", "walk", "wait", "buffer")}
    return {"minutes": math.ceil(duration / 60), "arrival": clock(cursor), "departure": clock(departure),
            "arrival_day": cursor.astimezone(zone()).strftime("%a, %b %-d"),
            "next_day": cursor.astimezone(zone()).date() != departure.astimezone(zone()).date(),
            "ride_minutes": totals["ride"], "walk_minutes": totals["walk"], "wait_minutes": totals["wait"],
            "buffer_minutes": totals["buffer"], "buses": len(bus_steps), "transfers": max(0, len(bus_steps) - 1),
            "lines": [s["line"] for s in bus_steps], "steps": itinerary,
            "warnings": list(dict.fromkeys(w for w in warnings if isinstance(w, str))), "end": cursor,
            "signature": tuple((s["title"], s["from"], s["to"], s["depart"]) for s in bus_steps)}


def combine_options(api, seed, origin, destination, departure, kind, max_ride):
    first_ride, last_ride = kind != "lime_last", kind != "lime"
    transit = buses(seed)
    boarding = stop(transit[0][1], "departure") if first_ride else origin
    alighting = stop(transit[-1][1], "arrival") if last_ride else destination
    itinerary, warnings, ready = [], [], departure
    if first_ride:
        access = riding(api, origin, boarding)
        if access["seconds"] > max_ride * 60:
            raise CandidateUnavailable
        ready = ride_segment(itinerary, ready, access, "Bike to boarding area" if kind == "bike" else "Scooter to boarding area (estimate)", kind != "bike")
        warnings.extend(access["warnings"])
    options = api.compute(boarding, alighting, "TRANSIT", ready, alternatives=True)
    results = []
    skipped = None
    for route in options[:3]:
        try:
            timeline = list(itinerary)
            cursor, trip_buses = transit_segments(route, timeline, ready, kind, last_ride)
            route_warnings = list(warnings)
            if last_ride:
                egress = riding(api, stop(trip_buses[-1][1], "arrival"), destination)
                if egress["seconds"] > max_ride * 60:
                    continue
                cursor = ride_segment(timeline, cursor, egress, "Bike to destination" if kind == "bike" else "Scooter to destination (estimate)", kind != "bike")
                route_warnings.extend(egress["warnings"])
            result = finish(timeline, departure, cursor, route_warnings)
            result["_transit_route"] = route
            results.append(result)
        except INVALID:
            continue
        except SearchLimit:
            if not results:
                raise
            break
        except MapsError as err:  # one stop Google can't route, or outside the area: skip only this option
            skipped = err
    if not results and skipped:
        raise skipped
    return results


def combine(api, seed, origin, destination, departure, kind, max_ride):
    """Compatibility helper for callers that need one schedule-checked option."""
    results = combine_options(api, seed, origin, destination, departure, kind, max_ride)
    if not results:
        raise CandidateUnavailable
    return min(results, key=lambda r: r["end"])


def nearby(location):
    p = point(location)["location"]["latLng"]
    lat, lon = p["latitude"], p["longitude"]
    nearby = sorted(anchors(), key=lambda a: (a[1] - lat) ** 2 + ((a[2] - lon) * .67) ** 2)
    return [address(name) for name, a_lat, a_lon in nearby[:2]
            if math.hypot((a_lat - lat) * 111, (a_lon - lon) * 75) <= 5]


def distinct(results):
    """Keep meaningful alternatives, not several near-identical stops on one bus."""
    selected = []
    for result in sorted(results, key=lambda r: (r["end"], r["transfers"], r["ride_minutes"])):
        if any(r["signature"] == result["signature"] or
               (r["lines"] == result["lines"] and abs(r["minutes"] - result["minutes"]) <= 3
                and abs(r["ride_minutes"] - result["ride_minutes"]) <= 3) for r in selected):
            continue
        # Drop strictly worse alternatives on the same set of bus lines.
        if any(r["lines"] == result["lines"] and r["minutes"] <= result["minutes"] and
               r["ride_minutes"] <= result["ride_minutes"] and r["walk_minutes"] <= result["walk_minutes"] for r in selected):
            continue
        selected.append(result)
    selected = selected[:5]
    if selected:
        for result in selected:
            result["badges"] = []
        selected[0]["badges"].append("Earliest arrival")
        least_ride = min(selected, key=lambda r: (r["ride_minutes"], r["minutes"]))
        if least_ride is not selected[0] and least_ride["ride_minutes"] < selected[0]["ride_minutes"]:
            least_ride["badges"].append("Less riding")
        least_transfers = min(selected, key=lambda r: (r["transfers"], r["minutes"]))
        if least_transfers is not selected[0] and least_transfers["transfers"] < selected[0]["transfers"]:
            least_transfers["badges"].append("Fewer transfers")
    return selected


def same_route_walking(api, result, origin, destination, departure):
    """Walk the endpoints but replay the EXACT stops, scheduled buses and transfers.

    Never request a new transit itinerary for the comparator. If walking cannot
    catch the first bus, show the earlier leave-by time instead of claiming a
    feasible same-departure trip or substituting a different bus.
    """
    route = result["_transit_route"]
    bus_steps = buses(route)
    first_index, first_bus = bus_steps[0]
    last_index, last_bus = bus_steps[-1]
    access = ground_route(api, origin, stop(first_bus, "departure"), "WALK")
    egress = ground_route(api, stop(last_bus, "arrival"), destination, "WALK")
    first_departure = timestamp(first_bus["stopDetails"]["departureTime"])
    leave_by = first_departure - timedelta(seconds=access["seconds"])
    actual_departure = min(departure, leave_by)
    timeline = []
    cursor = segment(timeline, actual_departure, "walk", "Walk to the same boarding stop", access["seconds"], link=access["link"])
    same_buses = {"legs": [{"steps": steps(route)[first_index:last_index + 1]}]}
    cursor, _ = transit_segments(same_buses, timeline, cursor, "walk", False)
    cursor = segment(timeline, cursor, "walk", "Walk from the same alighting stop", egress["seconds"], link=egress["link"])
    comparison = finish(timeline, actual_departure, cursor)
    if comparison["signature"] != result["signature"]:
        raise CandidateUnavailable
    comparison.update(state="same_departure" if actual_departure == departure else "leave_earlier",
                      leave_by=clock(leave_by), leave_by_day=leave_by.astimezone(zone()).strftime("%a, %b %-d"),
                      earlier_minutes=math.ceil((departure - actual_departure).total_seconds() / 60),
                      first_bus_departure=clock(first_departure))
    return comparison


def search(origin_text, destination_text, kind="bike", departure=None, max_ride=25, api=None):
    if kind not in KINDS or max_ride not in (10, 15, 25, 40):
        raise MapsError("Choose a supported ride type and riding limit.")
    origin, destination = address(origin_text), address(destination_text)
    if origin["address"].casefold() == destination["address"].casefold():
        raise MapsError("Start and destination are the same. Choose two different places.")
    departure = parse_departure(departure)
    owned = api is None
    transport = api or Routes()
    api = SearchCache(transport)
    results, tested, limited, skipped = [], set(), False, None
    try:
        baseline = api.compute(origin, destination, "TRANSIT", departure, alternatives=True)
        seeds = list(baseline[:2])
        # Discover corridors at BOTH ends: biking to a different bus and getting
        # off sooner can each beat Google's walking-access transit choice.
        if baseline:
            try:
                leg = baseline[0]["legs"][0]
                trips = [(anchor, destination) for anchor in nearby(leg["startLocation"])]
                if kind != "lime":
                    trips += [(origin, anchor) for anchor in nearby(leg["endLocation"])]
            except (*INVALID, MapsError):
                trips = []
            for start, end in trips:
                try:
                    seeds.extend(api.compute(start, end, "TRANSIT", departure)[:1])
                except SearchLimit:
                    raise
                except MapsError as err:  # a nearby corridor Google can't route; the others still count
                    skipped = err
                except INVALID:
                    continue
        for seed in seeds[:6]:
            try:
                trip = buses(seed)
                ident = (coordinates(stop(trip[0][1], "departure")) if kind != "lime_last" else "walk",
                         coordinates(stop(trip[-1][1], "arrival")) if kind != "lime" else "walk")
                if ident in tested:
                    continue
                if api.calls >= MAX_CALLS - 2:
                    limited = True
                    break
                tested.add(ident)
                results.extend(combine_options(api, seed, origin, destination, departure, kind, max_ride))
            except INVALID:
                continue
            except SearchLimit:
                limited = True
                break
            except MapsError as err:
                skipped = err
        results = distinct(results)
        if not results and skipped and not limited:
            raise skipped  # nothing to show, so say why rather than “no routes”
        for result in results:
            result["vs_bus"] = None
            try:
                comparison = same_route_walking(api, result, origin, destination, departure)
                result["walking_comparison"] = comparison
                if comparison["state"] == "same_departure":
                    result["vs_bus"] = comparison["minutes"] - result["minutes"]
            except SearchLimit:
                result["walking_comparison"] = {"state": "limit"}
            except (*INVALID, MapsError):
                result["walking_comparison"] = {"state": "unavailable"}
            finally:
                result.pop("_transit_route", None)
        return {"routes": results, "calls": api.calls, "checked": len(tested), "kind": kind,
                "limited": limited or api.limited, "checked_at": clock(datetime.now(timezone.utc)),
                "date": departure.astimezone(zone()).strftime("%a, %b %-d"), "departure": clock(departure)}
    finally:
        api.cache.clear()
        if owned:
            transport.close()

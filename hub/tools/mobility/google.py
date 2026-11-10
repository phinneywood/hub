"""Bounded Google Routes transport. No credentials, addresses or responses are logged."""
import json
import re
import subprocess
import time

import httpx

from hub import household

URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
MAX_CALLS = 24
MAX_BYTES = 1_000_000
FIELDS = ",".join((
    "routes.duration", "routes.distanceMeters", "routes.warnings",
    "routes.legs.startLocation", "routes.legs.endLocation",
    "routes.legs.steps.travelMode", "routes.legs.steps.staticDuration",
    "routes.legs.steps.distanceMeters", "routes.legs.steps.startLocation",
    "routes.legs.steps.endLocation", "routes.legs.steps.navigationInstruction",
    "routes.legs.steps.transitDetails",
))


class MapsError(Exception):
    pass


class SearchLimit(MapsError):
    pass


def key():
    try:
        result = subprocess.run(
            ["/usr/bin/security", "find-generic-password", "-s", "hub-google-maps", "-a", "hub", "-w"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    value = result.stdout.strip() if result.returncode == 0 else ""
    return value if re.fullmatch(r"[A-Za-z0-9_-]{20,200}", value) else None


def address(text):
    text = text.strip()
    if not 3 <= len(text) <= 240 or any(ord(c) < 32 for c in text):
        raise MapsError("Enter a start and destination, each between 3 and 240 characters.")
    return {"address": text}


def point(location):
    coordinates = location["latLng"]
    lat, lon = coordinates["latitude"], coordinates["longitude"]
    bounds = household.section("mobility").get("bounds") or {}
    if not all(isinstance(bounds.get(k), (int, float)) for k in ("south", "north", "west", "east")):
        raise MapsError("Set the planner's area in the private household file first.")
    if not (isinstance(lat, (int, float)) and isinstance(lon, (int, float))
            and bounds["south"] <= lat <= bounds["north"] and bounds["west"] <= lon <= bounds["east"]):
        raise MapsError("This planner supports only its configured city.")
    return {"location": {"latLng": {"latitude": lat, "longitude": lon}}}


class Routes:
    def __init__(self):
        self.token = key()
        if not self.token:
            raise MapsError("Maps key unavailable. Unlock the login Keychain or run deploy/set-maps-key.")
        self.calls = 0
        self.deadline = time.monotonic() + 90
        self.client = httpx.Client(timeout=15, follow_redirects=False, trust_env=False)

    def close(self):
        self.client.close()
        self.token = None

    def compute(self, origin, destination, mode, departure=None, alternatives=False):
        if self.calls >= MAX_CALLS or time.monotonic() >= self.deadline:
            raise SearchLimit("Search limit reached. Try a narrower search in a moment.")
        if mode not in ("TRANSIT", "BICYCLE", "WALK"):
            raise MapsError("Unsupported travel mode.")
        body = {"origin": origin, "destination": destination, "travelMode": mode,
                "languageCode": "en-US", "units": "IMPERIAL"}
        if mode == "TRANSIT":
            body.update(departureTime=departure.isoformat(), computeAlternativeRoutes=alternatives,
                        transitPreferences={"allowedTravelModes": ["BUS"]})
        self.calls += 1
        try:
            remaining = max(0.1, min(15, self.deadline - time.monotonic()))
            with self.client.stream("POST", URL, json=body, timeout=remaining,
                                    headers={"X-Goog-Api-Key": self.token, "X-Goog-FieldMask": FIELDS}) as response:
                if response.status_code != 200:
                    messages = {400: "Google couldn't route those locations. Check the addresses.",
                                401: "Google rejected the Maps key.",
                                403: "Google denied access. Check Routes API, billing and key restrictions.",
                                429: "Google's request quota was reached. Try later."}
                    raise MapsError(messages.get(response.status_code, "Google Routes is unavailable right now."))
                data = bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data) > MAX_BYTES or time.monotonic() >= self.deadline:
                        raise SearchLimit("Google's response exceeded the search limits.")
                payload = json.loads(data)
                routes = payload.get("routes", [])
                if not isinstance(routes, list) or len(routes) > 20 or any(not isinstance(r, dict) for r in routes):
                    raise ValueError
                return routes[:4]
        except (httpx.HTTPError, ValueError, TypeError, AttributeError):
            raise MapsError("Google Routes could not return a usable response.") from None

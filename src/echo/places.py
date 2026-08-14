"""
Find nearby places — free, no API keys.

Pipeline:
1. Detect approximate location by IP (ipapi.co — free, no key).
2. Search OpenStreetMap via the Overpass API for a category near that point.
3. Return the closest few (spoken) plus the full list (saved to a file).

All free and keyless. Location is IP-level (city/area accurate, not GPS), which
is fine for "cafes near me" style queries.
"""

from __future__ import annotations

import math
from pathlib import Path

import requests

from echo.config import ROOT

# multiple free IP-geolocation providers — we try each until one works, so a
# single provider being down or rate-limited doesn't break location detection.
IP_LOCATION_PROVIDERS = [
    ("https://ipapi.co/json/",
     lambda d: (d.get("latitude"), d.get("longitude"), d.get("city"),
                d.get("region"), d.get("country_name"))),
    ("https://ipwho.is/",
     lambda d: (d.get("latitude"), d.get("longitude"), d.get("city"),
                d.get("region"), d.get("country"))),
    ("http://ip-api.com/json/",
     lambda d: (d.get("lat"), d.get("lon"), d.get("city"),
                d.get("regionName"), d.get("country"))),
]
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
TIMEOUT = 15

# where to write the full results list the user can open
RESULTS_FILE = ROOT / "nearby_places.txt"

# map common spoken categories -> OpenStreetMap tags
_CATEGORY_TAGS = {
    "restaurant": ("amenity", "restaurant"),
    "restaurants": ("amenity", "restaurant"),
    "cafe": ("amenity", "cafe"),
    "cafes": ("amenity", "cafe"),
    "coffee": ("amenity", "cafe"),
    "hospital": ("amenity", "hospital"),
    "hospitals": ("amenity", "hospital"),
    "pharmacy": ("amenity", "pharmacy"),
    "pharmacies": ("amenity", "pharmacy"),
    "atm": ("amenity", "atm"),
    "atms": ("amenity", "atm"),
    "bank": ("amenity", "bank"),
    "banks": ("amenity", "bank"),
    "fuel": ("amenity", "fuel"),
    "petrol": ("amenity", "fuel"),
    "gas station": ("amenity", "fuel"),
    "hotel": ("tourism", "hotel"),
    "hotels": ("tourism", "hotel"),
    "supermarket": ("shop", "supermarket"),
    "grocery": ("shop", "supermarket"),
    "school": ("amenity", "school"),
    "park": ("leisure", "park"),
    "parks": ("leisure", "park"),
}


def _detect_location() -> dict | None:
    """Detect approximate location by IP, trying multiple providers in turn."""
    for url, parse in IP_LOCATION_PROVIDERS:
        try:
            r = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": "Echo"})
            r.raise_for_status()
            lat, lon, city, region, country = parse(r.json())
            if lat is not None and lon is not None:
                return {
                    "lat": float(lat),
                    "lon": float(lon),
                    "city": city or "",
                    "region": region or "",
                    "country": country or "",
                }
        except (requests.RequestException, ValueError, TypeError):
            continue  # try the next provider
    return None


def get_my_location() -> dict:
    """Report the user's approximate current location (IP-based)."""
    loc = _detect_location()
    if loc is None:
        return {"error": "couldn't detect your location right now."}
    parts = [p for p in (loc["city"], loc.get("region"), loc.get("country")) if p]
    where = ", ".join(parts) if parts else "an unknown place"
    return {
        "city": loc["city"],
        "region": loc.get("region", ""),
        "country": loc.get("country", ""),
        "latitude": loc["lat"],
        "longitude": loc["lon"],
        "spoken": f"You appear to be in {where}. "
        "Note this is based on your internet connection, so it's approximate.",
    }


def _haversine(lat1, lon1, lat2, lon2) -> float:
    """Distance in meters between two lat/lon points."""
    R = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def find_nearby_places(category: str, radius_m: int = 3000, city: str | None = None) -> dict:
    """
    Find places of a category near the user's IP location, or near `city` if
    given (geocoded the same way the weather tool resolves cities).

    Automatically widens the search (up to ~15km) if nothing is found nearby —
    helps in areas with sparse OpenStreetMap coverage or for categories that are
    naturally farther apart (like hospitals).

    Returns a dict with a spoken summary (top few) and the path to a saved file
    with the full list. `category` is a spoken word like 'restaurants' or 'atm'.
    """
    cat = (category or "").strip().lower()
    tag = _CATEGORY_TAGS.get(cat)
    if tag is None:
        return {
            "error": f"I don't know how to search for '{category}'. Try things "
            "like restaurants, cafes, hospitals, ATMs, pharmacies, or hotels."
        }

    if city and city.strip():
        from echo.weather import _geocode
        try:
            geo = _geocode(city.strip())
        except requests.RequestException as e:
            return {"error": f"couldn't reach the geocoding service: {e}"}
        if geo is None:
            return {"error": f"couldn't find a place called '{city}'. Ask the user to clarify."}
        loc = {
            "lat": geo["latitude"], "lon": geo["longitude"],
            "city": geo["name"], "region": "", "country": geo.get("country", ""),
        }
    else:
        loc = _detect_location()
        if loc is None:
            return {"error": "couldn't detect your location right now."}

    key, value = tag
    explicit_city = bool(city and city.strip())
    # widen the net until we find something (or give up at ~15km)
    for r_m in (radius_m, 6000, 15000):
        result = _search(key, value, loc, r_m, cat, explicit_city)
        if result.get("count", 0) > 0:
            return result
        last = result
    return last  # the last (empty) result, with its "couldn't find" message


def _search(key, value, loc, radius_m, cat, explicit_city: bool = False) -> dict:
    """One Overpass search at a given radius."""
    # search nodes, ways, AND relations — many places (restaurants, hospitals)
    # are tagged as ways/relations, not just points. `nwr` covers all three.
    # `out center` gives a representative coordinate for ways/relations.
    query = (
        f'[out:json][timeout:25];'
        f'nwr["{key}"="{value}"]'
        f'(around:{radius_m},{loc["lat"]},{loc["lon"]});'
        f'out center 40;'
    )
    try:
        # Overpass requires a User-Agent; sending the query as the raw body with
        # an explicit content type avoids the 406 "Not Acceptable" error.
        r = requests.post(
            OVERPASS_URL,
            data=query.encode("utf-8"),
            headers={
                "User-Agent": "Echo-Assistant/1.0",
                "Content-Type": "text/plain; charset=utf-8",
            },
            timeout=TIMEOUT + 20,
        )
        r.raise_for_status()
        elements = r.json().get("elements", [])
    except requests.RequestException as e:
        return {"error": f"couldn't reach the places service: {e}"}

    places = []
    for el in elements:
        name = el.get("tags", {}).get("name")
        if not name:
            continue
        # nodes have lat/lon directly; ways/relations have it under 'center'
        elat = el.get("lat") or el.get("center", {}).get("lat")
        elon = el.get("lon") or el.get("center", {}).get("lon")
        if elat is None or elon is None:
            continue
        dist = _haversine(loc["lat"], loc["lon"], elat, elon)
        places.append({"name": name, "distance_m": round(dist)})

    if not places:
        return {
            "spoken": f"I couldn't find any {cat} within "
            f"{radius_m // 1000} kilometers of "
            f"{loc.get('city') or 'your location'}."
        }

    places.sort(key=lambda p: p["distance_m"])

    # save the full list to a file the user can open
    city = loc.get("city") or "you"
    lines = [f"Nearby {cat} near {city}:", ""]
    for i, p in enumerate(places, 1):
        lines.append(f"{i}. {p['name']} — {p['distance_m']} m away")
    RESULTS_FILE.write_text("\n".join(lines), encoding="utf-8")

    # spoken summary: the closest few
    top = places[:3]
    parts = [f"{p['name']}, {p['distance_m']} meters away" for p in top]
    near = f"near {city}" if explicit_city else "near you"
    spoken = (
        f"I found {len(places)} {cat} {near}. The closest are: "
        + "; ".join(parts)
        + f". I've saved the full list of {len(places)} to a file."
    )

    return {
        "spoken": spoken,
        "count": len(places),
        "closest": top,
        "saved_to": str(RESULTS_FILE),
    }
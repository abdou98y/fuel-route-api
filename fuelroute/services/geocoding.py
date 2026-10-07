"""Turn user input / station cities into coordinates.

Resolution order (cheapest first):
1. ``"lat,lng"`` literals - no lookup at all.
2. ``"City, ST"`` (or ``"City, State Name"``) - resolved offline from the US Census
   gazetteer shipped in ``data/us_places.csv.gz``.
3. Anything else (street addresses, landmarks) - one Nominatim request, cached.
"""

import csv
import gzip
import hashlib
import math
import re
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path

import requests
from django.conf import settings
from django.core.cache import cache

GAZETTEER_PATH = Path(settings.BASE_DIR) / "data" / "us_places.csv.gz"

US_STATES = {
    "AL": "Alabama",
    "AK": "Alaska",
    "AZ": "Arizona",
    "AR": "Arkansas",
    "CA": "California",
    "CO": "Colorado",
    "CT": "Connecticut",
    "DE": "Delaware",
    "DC": "District of Columbia",
    "FL": "Florida",
    "GA": "Georgia",
    "HI": "Hawaii",
    "ID": "Idaho",
    "IL": "Illinois",
    "IN": "Indiana",
    "IA": "Iowa",
    "KS": "Kansas",
    "KY": "Kentucky",
    "LA": "Louisiana",
    "ME": "Maine",
    "MD": "Maryland",
    "MA": "Massachusetts",
    "MI": "Michigan",
    "MN": "Minnesota",
    "MS": "Mississippi",
    "MO": "Missouri",
    "MT": "Montana",
    "NE": "Nebraska",
    "NV": "Nevada",
    "NH": "New Hampshire",
    "NJ": "New Jersey",
    "NM": "New Mexico",
    "NY": "New York",
    "NC": "North Carolina",
    "ND": "North Dakota",
    "OH": "Ohio",
    "OK": "Oklahoma",
    "OR": "Oregon",
    "PA": "Pennsylvania",
    "RI": "Rhode Island",
    "SC": "South Carolina",
    "SD": "South Dakota",
    "TN": "Tennessee",
    "TX": "Texas",
    "UT": "Utah",
    "VT": "Vermont",
    "VA": "Virginia",
    "WA": "Washington",
    "WV": "West Virginia",
    "WI": "Wisconsin",
    "WY": "Wyoming",
}
STATE_BY_NAME = {name.lower(): code for code, name in US_STATES.items()}

# Legal/statistical area descriptions the Census appends to place names.
_LSAD_SUFFIX = re.compile(
    r"\s+(city and borough|consolidated government|unified government|metropolitan government"
    r"|metro government|urban county|charter township|city|town|township|village|borough|cdp"
    r"|municipality|plantation|gore|grant|location|purchase|unorganized territory|ccd)$",
    re.IGNORECASE,
)
_LAT_LNG = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")


class GeocodingError(Exception):
    """An input location is invalid or cannot be found."""


class GeocodingServiceError(GeocodingError):
    """External geocoding failed; the caller's input is not at fault."""


@dataclass(frozen=True)
class Location:
    query: str
    label: str
    latitude: float
    longitude: float
    source: str  # "coordinates" | "gazetteer" | "nominatim"

    def as_dict(self):
        return asdict(self)


def normalize_city(name: str) -> str:
    """Canonical key so "Mc Donald" == "McDonald", "Ft. Worth" == "Fort Worth", etc."""
    s = re.sub(r"\(.*?\)", " ", name.lower())
    s = s.replace("-", " ").replace(".", " ")
    s = re.sub(r"\bsaint\b", "st", s)
    s = re.sub(r"\bsainte\b", "ste", s)
    s = re.sub(r"\bft\b", "fort", s)
    s = re.sub(r"\bmt\b", "mount", s)
    s = re.sub(
        r"^([nsew])\s",
        lambda m: {"n": "north ", "s": "south ", "e": "east ", "w": "west "}[m[1]],
        s.strip(),
    )
    return re.sub(r"[^a-z]", "", s)


def _strip_lsad(name: str) -> str:
    """ "Dodge City city" -> "Dodge City" (only the trailing descriptor goes)."""
    return _LSAD_SUFFIX.sub("", re.sub(r"\s*\(.*?\)", "", name).strip()).strip()


@lru_cache(maxsize=1)
def city_index() -> dict[tuple[str, str], tuple[float, float]]:
    """(normalized city, state) -> (lat, lon), built once per process (~0.2 s).

    Priority: incorporated places / CDPs, then their aliases ("Nashville" for
    "Nashville-Davidson", "Honolulu" for "Urban Honolulu"), then county
    subdivisions (New England towns, townships).
    """
    with gzip.open(GAZETTEER_PATH, "rt", newline="") as f:
        rows = list(csv.DictReader(f))

    tiers: dict[str, list] = {
        "place": [],
        "place_alias": [],
        "cousub": [],
        "cousub_alias": [],
    }
    for row in rows:
        coords = (float(row["lat"]), float(row["lon"]))
        base = _strip_lsad(row["name"])
        tiers[row["kind"]].append(((normalize_city(base), row["state"]), coords))
        alias = base.split("-")[0] if "-" in base else base.removeprefix("Urban ")
        if alias != base:
            tiers[f"{row['kind']}_alias"].append(
                ((normalize_city(alias), row["state"]), coords)
            )

    index: dict[tuple[str, str], tuple[float, float]] = {}
    for entries in tiers.values():
        for key, coords in entries:
            index.setdefault(key, coords)
    return index


def lookup_city(city: str, state: str):
    return city_index().get((normalize_city(city), state.upper()))


def parse_state(token: str):
    token = token.strip().rstrip(".")
    if token.upper() in US_STATES:
        return token.upper()
    return STATE_BY_NAME.get(token.lower())


def search_nominatim(params: dict) -> tuple[float, float, str] | bool:
    """Shared HTTP/response boundary for user lookups and station import."""
    cfg = settings.FUEL_ROUTE
    try:
        response = requests.get(
            f"{cfg['NOMINATIM_BASE_URL']}/search",
            params={**params, "format": "jsonv2", "countrycodes": "us", "limit": 1},
            headers={"User-Agent": cfg["HTTP_USER_AGENT"]},
            timeout=cfg["HTTP_TIMEOUT_SECONDS"],
        )
        response.raise_for_status()
        results = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise GeocodingServiceError(f"Geocoding service unavailable: {exc}") from exc
    if not isinstance(results, list):
        raise GeocodingServiceError(
            "Geocoding service returned an invalid result list."
        )
    if not results:
        return False
    try:
        latitude, longitude = float(results[0]["lat"]), float(results[0]["lon"])
        label = results[0].get("display_name", params.get("q", params.get("city", "")))
        if (
            not math.isfinite(latitude)
            or not math.isfinite(longitude)
            or abs(latitude) > 90
            or abs(longitude) > 180
            or not isinstance(label, str)
        ):
            raise ValueError("Invalid coordinates or display name")
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise GeocodingServiceError(
            "Geocoding service returned a malformed location."
        ) from exc
    return latitude, longitude, label


def _nominatim(query: str):
    cache_key = "nominatim:" + hashlib.sha1(query.lower().encode()).hexdigest()
    hit = cache.get(cache_key)
    if hit is not None:
        return hit
    hit = search_nominatim({"q": query})
    cache.set(cache_key, hit, timeout=60 * 60 * 24 * 30)
    return hit


def is_in_usa_bounds(lat: float, lon: float) -> bool:
    """Rough check covering the contiguous US, Alaska and Hawaii."""
    contiguous = 24.3 <= lat <= 49.5 and -125.0 <= lon <= -66.8
    alaska = 51.0 <= lat <= 71.6 and (-180.0 <= lon <= -129.9 or 172.0 <= lon <= 180.0)
    hawaii = 18.8 <= lat <= 22.4 and -160.4 <= lon <= -154.7
    return contiguous or alaska or hawaii


def geocode(query: str) -> Location:
    query = query.strip()
    if not query:
        raise GeocodingError("Location must not be empty.")

    if match := _LAT_LNG.match(query):
        lat, lon = float(match[1]), float(match[2])
        location = Location(query, f"{lat:.5f}, {lon:.5f}", lat, lon, "coordinates")
    else:
        location = None
        city, _, rest = query.rpartition(",")
        state = parse_state(rest) if city else None
        if state and (coords := lookup_city(city, state)):
            location = Location(
                query, f"{city.strip().title()}, {state}", *coords, "gazetteer"
            )
        if location is None:
            hit = _nominatim(query)
            if not hit:
                raise GeocodingError(
                    f"Could not find a location in the USA matching '{query}'."
                )
            location = Location(query, hit[2], hit[0], hit[1], "nominatim")

    if not is_in_usa_bounds(location.latitude, location.longitude):
        raise GeocodingError(f"'{query}' resolves outside the USA.")
    return location

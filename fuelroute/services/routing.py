"""Thin client for the free OSRM routing API.

One HTTP call returns the primary route plus up to ``ROUTE_ALTERNATIVES``
alternatives; the planner then picks the cheapest one to drive.
"""

import hashlib
from dataclasses import dataclass
from math import isfinite
from typing import TYPE_CHECKING

import numpy as np
import requests
from django.conf import settings
from django.core.cache import cache

if TYPE_CHECKING:
    from .geocoding import Location

ROUTE_CACHE_SECONDS = 60 * 60 * 24 * 7

METERS_PER_MILE = 1609.344


class RoutingError(Exception):
    """The routing service failed (network error, outage...)."""


class NoRouteError(RoutingError):
    """The service works, but there is no drivable route between the points."""


@dataclass(frozen=True)
class Route:
    distance_miles: float
    duration_seconds: float
    latitudes: np.ndarray
    longitudes: np.ndarray


def get_routes(start: "Location", finish: "Location") -> tuple[list[Route], bool]:
    """Fetch the candidate routes, reusing a cached copy for the same endpoints."""
    cfg = settings.FUEL_ROUTE
    coords = f"{start.latitude:.5f},{start.longitude:.5f};{finish.latitude:.5f},{finish.longitude:.5f}"
    raw = f"{cfg['OSRM_BASE_URL']}|{cfg['ROUTE_ALTERNATIVES']}|{coords}"
    key = "routes:" + hashlib.sha1(raw.encode()).hexdigest()
    routes = cache.get(key)
    if routes is not None:
        return routes, True
    routes = fetch_routes(
        start.latitude, start.longitude, finish.latitude, finish.longitude
    )
    cache.set(key, routes, timeout=ROUTE_CACHE_SECONDS)
    return routes, False


def fetch_routes(start_lat, start_lon, end_lat, end_lon) -> list[Route]:
    """Return OSRM's recommended route followed by any alternatives."""
    cfg = settings.FUEL_ROUTE
    url = f"{cfg['OSRM_BASE_URL']}/route/v1/driving/{start_lon},{start_lat};{end_lon},{end_lat}"
    try:
        response = requests.get(
            url,
            params={
                "overview": "full",
                "geometries": "geojson",
                "steps": "false",
                "alternatives": cfg["ROUTE_ALTERNATIVES"],
            },
            headers={"User-Agent": cfg["HTTP_USER_AGENT"]},
            timeout=cfg["HTTP_TIMEOUT_SECONDS"],
        )
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise RoutingError(f"Routing service unavailable: {exc}") from exc

    if not isinstance(payload, dict):
        raise RoutingError("Routing service returned an invalid response object.")
    code = payload.get("code")
    if not isinstance(code, str):
        raise RoutingError("Routing service returned an invalid response code.")
    if code in {"NoRoute", "NoSegment"}:
        raise NoRouteError("No drivable route found between these locations.")
    if response.status_code != 200 or code != "Ok" or not payload.get("routes"):
        message = payload.get("message") or code or f"HTTP {response.status_code}"
        raise RoutingError(f"Routing service error: {message}")

    if not isinstance(payload["routes"], list):
        raise RoutingError("Routing service returned an invalid route list.")
    return [route_from_payload(route) for route in payload["routes"]]


def route_from_payload(route: dict) -> Route:
    """Validate external fields before they become a trusted route object."""
    try:
        coords = np.asarray(route["geometry"]["coordinates"], dtype=float)
        distance, duration = route["distance"], route["duration"]
        metrics = (distance, duration)
        valid_metrics = all(
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and isfinite(value)
            and value >= 0
            for value in metrics
        )
        valid_geometry = (
            coords.ndim == 2
            and coords.shape[0] >= 2
            and coords.shape[1] == 2
            and np.isfinite(coords).all()
            and (np.abs(coords[:, 0]) <= 180).all()
            and (np.abs(coords[:, 1]) <= 90).all()
        )
        if not valid_metrics or not valid_geometry:
            raise ValueError("Invalid distance, duration or coordinates")
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise RoutingError("Routing service returned a malformed route.") from exc
    return Route(
        distance_miles=distance / METERS_PER_MILE,
        duration_seconds=duration,
        latitudes=coords[:, 1],
        longitudes=coords[:, 0],
    )

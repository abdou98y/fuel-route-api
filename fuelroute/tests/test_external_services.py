"""External failures must stay inside the clients' documented error boundary."""

from copy import deepcopy
from unittest.mock import Mock, patch

import pytest
import requests
from django.core.cache import cache
from rest_framework.test import APIClient

from fuelroute.services.geocoding import (
    GeocodingError,
    GeocodingServiceError,
    geocode,
)
from fuelroute.services.routing import NoRouteError, RoutingError, fetch_routes

VALID_ROUTE = {
    "distance": 160934.4,
    "duration": 3600,
    "geometry": {"coordinates": [[-80, 40], [-81, 41]]},
}


@pytest.fixture(autouse=True)
def isolated_cache(settings):
    settings.CACHES = {
        "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
    }
    cache.clear()


def response(payload, status=200):
    result = Mock(status_code=status)
    result.json.return_value = payload
    return result


@pytest.mark.parametrize(
    "payload",
    [
        [],
        None,
        {"code": "Ok", "routes": "invalid"},
        {"code": "Ok", "routes": [{}]},
        {"code": "Ok", "routes": [None]},
        {"code": "Ok", "routes": []},
        {"code": [], "routes": [VALID_ROUTE]},
    ],
)
def test_malformed_osrm_payload_becomes_routing_error(payload):
    with patch(
        "fuelroute.services.routing.requests.get", return_value=response(payload)
    ):
        with pytest.raises(RoutingError):
            fetch_routes(40, -80, 41, -81)


@pytest.mark.parametrize(
    "field,value",
    [
        ("distance", -1),
        ("distance", float("nan")),
        ("distance", True),
        ("duration", float("inf")),
        ("duration", "bad"),
        ("geometry", {"coordinates": []}),
        ("geometry", {"coordinates": [[-80, 40], [-81]]}),
        ("geometry", {"coordinates": [[-80, 40, 0], [-81, 41, 0]]}),
        ("geometry", {"coordinates": [[-80, 40], [-181, 41]]}),
        ("geometry", {"coordinates": [[-80, 40], [-81, float("nan")]]}),
    ],
)
def test_malformed_route_fields_become_routing_error(field, value):
    route = deepcopy(VALID_ROUTE)
    route[field] = value
    with patch(
        "fuelroute.services.routing.requests.get",
        return_value=response({"code": "Ok", "routes": [route]}),
    ):
        with pytest.raises(RoutingError):
            fetch_routes(40, -80, 41, -81)


def test_osrm_valid_alternatives_are_returned_in_one_call():
    with patch(
        "fuelroute.services.routing.requests.get",
        return_value=response({"code": "Ok", "routes": [VALID_ROUTE] * 4}),
    ) as http:
        routes = fetch_routes(40, -80, 41, -81)
    assert len(routes) == 4
    assert routes[0].distance_miles == pytest.approx(100)
    http.assert_called_once()
    assert http.call_args.kwargs["params"]["alternatives"] == 3


@pytest.mark.parametrize("code", ["NoRoute", "NoSegment"])
def test_osrm_no_route_is_distinct_from_service_failure(code):
    with patch(
        "fuelroute.services.routing.requests.get",
        return_value=response({"code": code}, 400),
    ):
        with pytest.raises(NoRouteError):
            fetch_routes(40, -80, 41, -81)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        [None],
        [{}],
        [{"lat": "nan", "lon": "-80"}],
        [{"lat": "40", "lon": "inf"}],
        [{"lat": "91", "lon": "-80"}],
        [{"lat": "40", "lon": "-80", "display_name": []}],
    ],
)
def test_malformed_nominatim_payload_becomes_service_error(payload):
    with patch(
        "fuelroute.services.geocoding.requests.get", return_value=response(payload)
    ):
        with pytest.raises(GeocodingServiceError):
            geocode("an uncached street address")


def test_location_not_found_remains_an_input_error():
    with patch("fuelroute.services.geocoding.requests.get", return_value=response([])):
        with pytest.raises(GeocodingError) as error:
            geocode("a nonexistent address")
    assert not isinstance(error.value, GeocodingServiceError)


@pytest.mark.parametrize("endpoint", ["/api/route/", "/api/route/map/"])
def test_nominatim_timeout_returns_502(endpoint):
    with patch(
        "fuelroute.services.geocoding.requests.get",
        side_effect=requests.Timeout("timeout"),
    ):
        result = APIClient().get(
            endpoint, {"start": "an uncached street address", "finish": "Chicago, IL"}
        )
    assert result.status_code == 502


def test_malformed_osrm_response_returns_502():
    with patch(
        "fuelroute.services.routing.requests.get",
        return_value=response({"code": "Ok", "routes": [{}]}),
    ):
        result = APIClient().get("/api/route/", {"start": "40,-80", "finish": "41,-81"})
    assert result.status_code == 502


def test_nominatim_result_is_cached_for_repeat_lookup():
    payload = [{"lat": "40", "lon": "-80", "display_name": "An address"}]
    with patch(
        "fuelroute.services.geocoding.requests.get", return_value=response(payload)
    ) as http:
        first = geocode("an uncached street address")
        second = geocode("an uncached street address")
    http.assert_called_once()
    assert first == second

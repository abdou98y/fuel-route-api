from decimal import Decimal
from unittest import mock

import numpy as np
import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

from fuelroute.models import FuelStation
from fuelroute.services.routing import Route
from fuelroute.services.station_index import clear_station_index

# A straight-ish fake route due west along latitude 40 (~53 miles per degree).
ROUTE = Route(
    distance_miles=float(np.sum(np.full(20, 53.0))),  # ~1060 miles
    duration_seconds=60_000,
    latitudes=np.full(21, 40.0),
    longitudes=np.linspace(-80.0, -100.0, 21),
)


@pytest.fixture(autouse=True)
def isolated(settings, db):
    settings.CACHES = {
        "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
    }
    cache.clear()  # locmem storage is shared by every test in the process
    clear_station_index()
    yield
    clear_station_index()


@pytest.fixture
def stations(db):
    rows = [
        (1, "ON ROUTE CHEAP", -86.0, 40.02, "2.50"),
        (2, "ON ROUTE PRICEY", -87.0, 40.0, "4.00"),
        (3, "ON ROUTE MID", -93.0, 40.05, "3.10"),
        (4, "FAR AWAY CHEAPEST", -86.0, 43.0, "1.00"),  # ~200 miles off the route
    ]
    for opis_id, name, lon, lat, price in rows:
        FuelStation.objects.create(
            opis_id=opis_id,
            name=name,
            address="I-80",
            city="Somewhere",
            state="OH",
            retail_price=Decimal(price),
            latitude=lat,
            longitude=lon,
        )


def plan(client, **params):
    with mock.patch(
        "fuelroute.services.routing.fetch_routes", return_value=[ROUTE]
    ) as fetch:
        response = client.get("/api/route/", params)
    return response, fetch


def test_plan_route(stations):
    response, fetch = plan(APIClient(), start="40,-80", finish="40,-100")
    assert response.status_code == 200, response.content
    body = response.json()

    fetch.assert_called_once()
    assert [s["name"] for s in body["fuel_stops"]] == ["ON ROUTE CHEAP", "ON ROUTE MID"]
    assert body["summary"]["total_fuel_cost"] == pytest.approx(
        sum(s["cost"] for s in body["fuel_stops"]), abs=0.02
    )
    assert body["route"]["distance_miles"] == pytest.approx(1060)
    assert body["map"]["type"] == "FeatureCollection"
    assert body["map_url"].startswith("http://testserver/api/route/map/?")


def test_route_is_fetched_once_then_cached(stations):
    client = APIClient()
    plan(client, start="40,-80", finish="40,-100")
    response, fetch = plan(client, start="40,-80", finish="40,-100")
    fetch.assert_not_called()
    assert response.json()["meta"]["routing_api_calls"] == 0


def test_post_body_is_supported(stations):
    with mock.patch("fuelroute.services.routing.fetch_routes", return_value=[ROUTE]):
        response = APIClient().post(
            "/api/route/", {"start": "40,-80", "finish": "40,-100"}, format="json"
        )
    assert response.status_code == 200


def test_map_view_renders_html(stations):
    with mock.patch("fuelroute.services.routing.fetch_routes", return_value=[ROUTE]):
        response = APIClient().get(
            "/api/route/map/", {"start": "40,-80", "finish": "40,-100"}
        )
    assert response.status_code == 200
    assert b"leaflet" in response.content and b"ON ROUTE CHEAP" in response.content


def test_missing_params_return_400():
    response = APIClient().get("/api/route/", {"start": "Chicago, IL"})
    assert response.status_code == 400
    assert "finish" in response.json()["errors"]


def test_location_outside_usa_returns_400():
    response = APIClient().get(
        "/api/route/", {"start": "48.85,2.35", "finish": "Chicago, IL"}
    )
    assert response.status_code == 400


def test_unreachable_gap_returns_422(db):
    response, _ = plan(
        APIClient(), start="40,-80", finish="40,-100"
    )  # no stations at all
    assert response.status_code == 422

from decimal import Decimal
from unittest import mock

import numpy as np
import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

from fuelroute.models import FuelStation
from fuelroute.services.geo import cumulative_miles
from fuelroute.services.routing import Route
from fuelroute.services.station_index import clear_station_index


def straight_route(lat, seconds):
    """A fake route due west from -80 to -100 longitude along a fixed latitude."""
    lats, lons = np.full(41, float(lat)), np.linspace(-80.0, -100.0, 41)
    return Route(float(cumulative_miles(lats, lons)[-1]), seconds, lats, lons)


FASTEST = straight_route(40, 60_000)  # ~1,060 mi; stations at $2.50, $4.00, $3.10
CHEAP_ALT = straight_route(42, 63_000)  # 5% slower; stations at $2.00
SLOW_ALT = straight_route(42, 70_000)  # 17% slower


@pytest.fixture(autouse=True)
def stations(settings, db):
    settings.CACHES = {
        "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
    }
    cache.clear()  # locmem storage is shared by every test in the process
    clear_station_index()
    rows = [
        (1, "LAT40 CHEAP", 40.0, -86.0, "2.50"),
        (2, "LAT40 PRICEY", 40.0, -87.0, "4.00"),
        (3, "LAT40 MID", 40.0, -93.0, "3.10"),
        (4, "LAT42 A", 42.0, -86.0, "2.00"),
        (5, "LAT42 B", 42.0, -93.0, "2.00"),
    ]
    for opis_id, name, lat, lon, price in rows:
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
    yield
    clear_station_index()


def plan(routes, **params):
    params = {"start": "40,-80", "finish": "40,-100", **params}
    with mock.patch(
        "fuelroute.services.routing.fetch_routes", return_value=routes
    ) as fetch:
        response = APIClient().get("/api/route/", params)
    assert (
        fetch.call_count <= 1
    )  # alternatives come back in the same single call (0 = cached)
    return response


def test_picks_alternative_when_savings_cover_extra_driving():
    body = plan([FASTEST, CHEAP_ALT]).json()
    selection = body["route_selection"]
    assert body["route"]["route_index"] == 1
    assert {s["name"] for s in body["fuel_stops"]} <= {"LAT42 A", "LAT42 B"}
    assert [o["selected"] for o in selection["options"]] == [False, True]
    assert selection["options"][1]["fuel_cost"] < selection["options"][0]["fuel_cost"]
    assert (
        selection["options"][1]["total_operating_cost"]
        < selection["options"][0]["total_operating_cost"]
    )
    assert "total operating cost" in selection["reason"]
    kinds = [f["properties"]["kind"] for f in body["map"]["features"]]
    assert kinds.count("alternative") == 1 and kinds.count("route") == 1


def test_keeps_fastest_when_cheaper_alternative_is_too_slow():
    body = plan([FASTEST, SLOW_ALT]).json()
    assert body["route"]["route_index"] == 0
    assert "lowest total operating cost" in body["route_selection"]["reason"]


def test_hourly_cost_is_configurable(settings):
    settings.FUEL_ROUTE = {**settings.FUEL_ROUTE, "DRIVER_TRUCK_COST_PER_HOUR": 5}
    assert plan([FASTEST, SLOW_ALT]).json()["route"]["route_index"] == 1


def test_falls_back_to_feasible_route_when_fastest_has_a_gap():
    gap = Route(
        FASTEST.distance_miles, 50_000, np.full(41, 30.0), np.linspace(-80, -100, 41)
    )  # no stations
    body = plan([gap, SLOW_ALT]).json()
    assert body["route"]["route_index"] == 1
    assert body["route_selection"]["options"][0]["error"]


def test_default_start_fuel_is_a_full_tank():
    body = plan([FASTEST]).json()
    assert body["vehicle"]["start_fuel_gallons"] == 50
    assert body["summary"]["gallons_used_from_start_tank"] == pytest.approx(
        50, abs=0.05
    )


def test_less_start_fuel_means_more_fuel_bought():
    full = plan([FASTEST]).json()["summary"]
    low = plan([FASTEST], start_fuel_gallons=35).json()["summary"]
    assert low["total_gallons_purchased"] == pytest.approx(
        full["total_gallons_purchased"] + 15, abs=0.05
    )
    assert low["total_fuel_cost"] > full["total_fuel_cost"]


def test_estimated_trip_cost_includes_starting_fuel():
    body = plan([FASTEST]).json()
    summary = body["summary"]
    assert body["vehicle"]["start_fuel_price_per_gallon"] == 2.0
    assert body["vehicle"]["start_fuel_price_source"] == "lowest_imported_station_price"
    assert summary["estimated_trip_fuel_cost"] == pytest.approx(
        summary["total_fuel_cost"] + summary["gallons_used_from_start_tank"] * 2.0,
        abs=0.05,
    )


def test_short_trip_still_reports_trip_fuel_cost():
    # 300-mile trip on a full tank: nothing bought, but the trip still burns 30 gal.
    short = Route(300.0, 20_000, np.full(11, 40.0), np.linspace(-80.0, -85.6, 11))
    summary = plan([short]).json()["summary"]
    assert summary["total_fuel_cost"] == 0
    assert summary["estimated_trip_fuel_cost"] > 0


def test_not_enough_start_fuel_returns_422():
    response = plan(
        [FASTEST], start_fuel_gallons=5
    )  # 50 miles; first station is ~318 miles out
    assert response.status_code == 422
    assert "Starting fuel only covers 50 miles" in response.json()["error"]


@pytest.mark.parametrize("value", ["-1", "51", "abc", "nan", "inf", "-inf"])
def test_invalid_start_fuel_returns_400(value):
    response = APIClient().get(
        "/api/route/",
        {"start": "40,-80", "finish": "40,-100", "start_fuel_gallons": value},
    )
    assert response.status_code == 400
    assert "start_fuel_gallons" in response.json()["errors"]


def test_map_url_keeps_start_fuel():
    body = plan([FASTEST], start_fuel_gallons=40).json()
    assert "start_fuel_gallons=40" in body["map_url"]


def test_user_start_price_is_used_and_preserved_in_map_url():
    body = plan([FASTEST], start_fuel_price_per_gallon=3.25).json()
    assert body["vehicle"]["start_fuel_price_source"] == "provided"
    assert body["summary"]["start_tank_fuel_value"] == 162.5
    assert "start_fuel_price_per_gallon=3.25" in body["map_url"]


@pytest.mark.parametrize("value", ["-1", "abc", "nan", "inf", "-inf"])
def test_invalid_start_price_returns_400(value):
    response = APIClient().get(
        "/api/route/",
        {"start": "40,-80", "finish": "40,-100", "start_fuel_price_per_gallon": value},
    )
    assert response.status_code == 400
    assert "start_fuel_price_per_gallon" in response.json()["errors"]


def test_short_trip_values_only_consumed_starting_fuel():
    short = Route(300, 20_000, np.full(41, 40.0), np.linspace(-80, -85.6, 41))
    summary = plan([short], start_fuel_price_per_gallon=3).json()["summary"]
    assert summary["estimated_trip_fuel_cost"] == 90
    assert summary["gallons_used_from_start_tank"] == 30


def test_post_accepts_start_price():
    with mock.patch("fuelroute.services.routing.fetch_routes", return_value=[FASTEST]):
        response = APIClient().post(
            "/api/route/",
            {
                "start": "40,-80",
                "finish": "40,-100",
                "start_fuel_price_per_gallon": 3.25,
            },
            format="json",
        )
    assert response.status_code == 200
    assert response.json()["vehicle"]["start_fuel_price_per_gallon"] == 3.25


def test_same_starting_price_used_for_all_roads():
    body = plan([FASTEST, CHEAP_ALT], start_fuel_price_per_gallon=4).json()
    for option in body["route_selection"]["options"]:
        assert option["estimated_trip_fuel_cost"] - option[
            "fuel_cost"
        ] == pytest.approx(200, abs=0.02)


def test_cached_routes_replanned_with_new_start_price():
    first = plan([FASTEST], start_fuel_price_per_gallon=2).json()
    second = plan([FASTEST], start_fuel_price_per_gallon=4).json()
    assert second["meta"]["routing_api_calls"] == 0
    assert second["summary"]["estimated_trip_fuel_cost"] == pytest.approx(
        first["summary"]["estimated_trip_fuel_cost"] + 100
    )


def test_operating_total_is_sum_of_components():
    body = plan([FASTEST]).json()
    summary = body["summary"]
    assert summary["estimated_driving_cost"] == pytest.approx(
        FASTEST.duration_seconds / 3600 * 50, abs=0.01
    )
    assert summary["estimated_total_operating_cost"] == pytest.approx(
        summary["estimated_trip_fuel_cost"]
        + summary["estimated_driving_cost"]
        + summary["estimated_stop_cost"],
        abs=0.02,
    )


def _selection_option(index, route, purchase):
    from fuelroute.services.configuration import get_planning_config
    from fuelroute.services.costs import calculate_costs
    from fuelroute.services.route_selection import RouteOption
    from fuelroute.services.station_matching import StationMatches

    config = get_planning_config()
    purchases = (purchase,)
    costs = calculate_costs(
        purchases,
        route.distance_miles,
        route.duration_seconds,
        0,
        config.vehicle,
        config.costs,
    )
    return RouteOption(
        index, route, StationMatches.empty(), purchases, costs, config.costs
    )


def test_route_comparison_matches_fifty_dollars_per_hour_example(settings):
    from fuelroute.services.optimizer import Candidate, Purchase
    from fuelroute.services.route_selection import choose_route

    settings.FUEL_ROUTE = {**settings.FUEL_ROUTE, "STOP_PENALTY_USD": 0}

    def option(i, hours, cost):
        route = Route(1000, hours * 3600, np.array([40, 40]), np.array([-80, -100]))
        return _selection_option(
            i, route, Purchase(Candidate(i, 300, 3), cost / 3, cost, 0)
        )

    fastest = option(0, 20, 300)
    alternative = option(1, 21.4, 287.48)  # saves $12.52, extra time costs $70
    assert choose_route([fastest, alternative]) is fastest
    alternative = _selection_option(
        1, alternative.route, Purchase(Candidate(1, 300, 3), 200 / 3, 200, 0)
    )
    assert choose_route([fastest, alternative]) is alternative


def test_equal_operating_cost_prefers_faster_route(settings):
    from fuelroute.services.optimizer import Candidate, Purchase
    from fuelroute.services.route_selection import choose_route

    settings.FUEL_ROUTE = {**settings.FUEL_ROUTE, "STOP_PENALTY_USD": 0}

    def option(i, seconds, fuel_cost):
        route = Route(1000, seconds, np.array([40, 40]), np.array([-80, -100]))
        return _selection_option(
            i, route, Purchase(Candidate(i, 300, 3), fuel_cost / 3, fuel_cost, 0)
        )

    fastest, alternative = option(0, 3600, 100), option(1, 7200, 50)
    assert choose_route([alternative, fastest]) is fastest


def test_starting_price_affects_short_route_choice():
    fastest = Route(180, 3600, np.array([40, 40]), np.array([-80, -83]))
    shorter = Route(120, 4320, np.array([40, 40]), np.array([-80, -82]))
    free = plan([fastest, shorter], start_fuel_price_per_gallon=0).json()
    priced = plan([fastest, shorter], start_fuel_price_per_gallon=3).json()
    # Saving six gallons ($18) covers 12 extra minutes ($10) at $50/hour.
    assert free["route"]["route_index"] == 0
    assert priced["route"]["route_index"] == 1
    assert priced["summary"]["total_fuel_cost"] == 0
    assert priced["summary"]["estimated_trip_fuel_cost"] == 36

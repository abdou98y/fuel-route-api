from dataclasses import FrozenInstanceError
from math import inf
from unittest.mock import patch

import numpy as np
import pytest

from fuelroute.serializers import RoutePlanRequestSerializer
from fuelroute.services.configuration import get_planning_config
from fuelroute.services.costs import CostConfig, VehicleConfig, calculate_costs
from fuelroute.services.optimizer import Candidate, InfeasibleRouteError, Purchase
from fuelroute.services.planner import build_plan, plan_route
from fuelroute.services.route_selection import RouteOption, choose_route
from fuelroute.services.routing import Route
from fuelroute.services.station_index import clear_station_index, get_station_index
from fuelroute.services.station_matching import StationMatches


def short_route():
    return Route(100, 3600, np.array([40, 40]), np.array([-80, -81.8]))


def test_costs_include_only_consumed_starting_fuel():
    costs = calculate_costs((), 100, 3600, 3, VehicleConfig(500, 10), CostConfig(50, 5))
    assert costs.trip_fuel_cost == 30
    assert costs.gallons_used_from_start_tank == 10
    assert costs.total_operating_cost == 80


def test_costs_account_for_purchases_and_stop_allowance():
    purchases = (Purchase(Candidate(0, 400, 3), 20, 60, 10),)
    costs = calculate_costs(
        purchases, 700, 36000, 2, VehicleConfig(500, 10), CostConfig(50, 5)
    )
    assert costs.gallons_used_from_start_tank == 50
    assert costs.trip_fuel_cost == 160
    assert costs.total_operating_cost == 665


def test_option_costs_do_not_change_when_django_settings_change(settings):
    config = get_planning_config()
    route = short_route()
    costs = calculate_costs(
        (),
        route.distance_miles,
        route.duration_seconds,
        3,
        config.vehicle,
        config.costs,
    )
    option = RouteOption(0, route, StationMatches.empty(), (), costs, config.costs)
    settings.FUEL_ROUTE = {
        **settings.FUEL_ROUTE,
        "DRIVER_TRUCK_COST_PER_HOUR": 999,
        "STOP_PENALTY_USD": 999,
    }
    assert option.score == 80
    assert option.driving_cost == 50
    with pytest.raises(FrozenInstanceError):
        option.purchases = ()


def test_infeasible_option_has_explicit_infinite_score():
    option = RouteOption(
        0, short_route(), StationMatches.empty(), None, None, CostConfig(50, 5), "gap"
    )
    assert option.score == inf
    with pytest.raises(InfeasibleRouteError, match="gap"):
        choose_route([option])


def test_inconsistent_option_cannot_be_created():
    with pytest.raises(ValueError, match="both purchases and costs"):
        RouteOption(
            0, short_route(), StationMatches.empty(), (), None, CostConfig(50, 5)
        )


def test_empty_route_selection_has_clear_error():
    with pytest.raises(InfeasibleRouteError, match="No candidate"):
        choose_route([])


def test_station_match_arrays_must_be_aligned():
    with pytest.raises(ValueError, match="aligned"):
        StationMatches(np.array([0]), np.array([]), np.array([]))


@pytest.mark.parametrize(
    "range_miles,mpg,fuel,valid",
    [
        (600, 10, 55, True),
        (400, 20, 25, False),
    ],
)
def test_request_validation_uses_current_vehicle_config(
    settings, range_miles, mpg, fuel, valid
):
    settings.FUEL_ROUTE = {
        **settings.FUEL_ROUTE,
        "VEHICLE_RANGE_MILES": range_miles,
        "VEHICLE_MPG": mpg,
    }
    serializer = RoutePlanRequestSerializer(
        data={
            "start": "Chicago, IL",
            "finish": "Denver, CO",
            "start_fuel_gallons": fuel,
        }
    )
    assert serializer.is_valid() is valid


def test_request_validation_and_planning_share_vehicle_snapshot(settings):
    original = get_planning_config()
    settings.FUEL_ROUTE = {**settings.FUEL_ROUTE, "VEHICLE_RANGE_MILES": 100}
    serializer = RoutePlanRequestSerializer(
        data={"start": "Chicago, IL", "finish": "Denver, CO", "start_fuel_gallons": 40},
        context={"vehicle": original.vehicle},
    )
    assert serializer.is_valid()


@pytest.mark.django_db
def test_request_uses_one_station_snapshot_throughout():
    clear_station_index()
    snapshot = get_station_index()
    with (
        patch(
            "fuelroute.services.planner.get_station_index", return_value=snapshot
        ) as load,
        patch(
            "fuelroute.services.planner.get_routes",
            return_value=([short_route()], True),
        ),
    ):
        result = build_plan("40,-80", "40,-81.8", start_fuel_price_per_gallon=3)
    load.assert_called_once()
    assert result["summary"]["estimated_trip_fuel_cost"] == 30


@pytest.mark.django_db
def test_explicit_snapshots_allow_planning_without_global_loads(settings):
    clear_station_index()
    snapshot = get_station_index()
    config = get_planning_config()
    settings.FUEL_ROUTE = {**settings.FUEL_ROUTE, "DRIVER_TRUCK_COST_PER_HOUR": 999}
    option = plan_route(0, short_route(), 500, 3, stations=snapshot, config=config)
    assert option.score == 80


@pytest.mark.django_db
def test_clearing_cache_preserves_existing_read_only_snapshot():
    clear_station_index()
    snapshot = get_station_index()
    clear_station_index()
    assert get_station_index() is not snapshot
    assert not snapshot.prices.flags.writeable
    assert isinstance(snapshot.names, tuple)

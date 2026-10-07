"""Coordinate location lookup, routing, per-road planning and presentation."""

import time

import numpy as np

from .configuration import PlanningConfig, get_planning_config
from .costs import calculate_costs
from .geocoding import geocode
from .optimizer import Candidate, InfeasibleRouteError, plan_fuel_stops
from .presentation import build_response
from .route_selection import RouteOption, choose_route
from .routing import Route, get_routes
from .station_index import StationIndex, get_station_index
from .station_matching import stations_along_route


def plan_route(
    route_index: int,
    route: Route,
    start_fuel_miles: float,
    start_fuel_price_per_gallon: float = 0.0,
    *,
    stations: StationIndex,
    config: PlanningConfig,
) -> RouteOption:
    matches = stations_along_route(
        route,
        stations,
        config.max_station_distance_miles,
        config.route_sample_spacing_miles,
    )
    candidates = [
        Candidate(
            key=match_position,
            mile=float(marker),
            price=float(stations.prices[station_position]),
        )
        for match_position, (station_position, marker) in enumerate(
            zip(matches.station_positions, matches.mile_markers)
        )
    ]
    try:
        purchases = tuple(
            plan_fuel_stops(
                candidates,
                route.distance_miles,
                config.vehicle.range_miles,
                config.vehicle.mpg,
                start_fuel_miles=start_fuel_miles,
                stop_penalty=config.costs.stop_penalty_usd,
            )
        )
    except InfeasibleRouteError as exc:
        return RouteOption(
            index=route_index,
            route=route,
            matches=matches,
            purchases=None,
            costs=None,
            cost_config=config.costs,
            error=str(exc),
        )
    costs = calculate_costs(
        purchases,
        route.distance_miles,
        route.duration_seconds,
        start_fuel_price_per_gallon,
        config.vehicle,
        config.costs,
    )
    return RouteOption(
        index=route_index,
        route=route,
        matches=matches,
        purchases=purchases,
        costs=costs,
        cost_config=config.costs,
    )


def build_plan(
    start_query: str,
    finish_query: str,
    start_fuel_gallons: float | None = None,
    start_fuel_price_per_gallon: float | None = None,
    *,
    config: PlanningConfig | None = None,
) -> dict:
    started = time.perf_counter()
    config = config if config is not None else get_planning_config()
    start_gallons = (
        config.vehicle.tank_gallons
        if start_fuel_gallons is None
        else min(start_fuel_gallons, config.vehicle.tank_gallons)
    )
    start, finish = geocode(start_query), geocode(finish_query)
    routes, routes_cached = get_routes(start, finish)
    # One stable station snapshot for matching, costing and presenting all roads.
    stations = get_station_index()
    if start_fuel_price_per_gallon is None:
        if not len(stations):
            raise InfeasibleRouteError(
                "No station prices available to value starting fuel; provide start_fuel_price_per_gallon."
            )
        start_price = float(np.min(stations.prices))
        start_price_source = "lowest_imported_station_price"
    else:
        start_price = start_fuel_price_per_gallon
        start_price_source = "provided"
    options = [
        plan_route(
            route_index,
            route,
            start_gallons * config.vehicle.mpg,
            start_price,
            stations=stations,
            config=config,
        )
        for route_index, route in enumerate(routes)
    ]
    chosen = choose_route(options)
    response = build_response(
        start,
        finish,
        chosen,
        options,
        stations,
        config,
        start_gallons,
        start_price,
        start_price_source,
        routes_cached,
    )
    response["meta"]["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 1)
    return response

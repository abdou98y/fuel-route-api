"""Present calculated plans as JSON fields, explanations and GeoJSON."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .geo import resample_polyline
from .route_selection import RouteOption
from .station_index import StationIndex

if TYPE_CHECKING:
    from .configuration import PlanningConfig
    from .geocoding import Location
    from .routing import Route

ALTERNATIVE_GEOMETRY_SPACING_MILES = 5.0


def describe_selection(chosen: RouteOption, options: list[RouteOption]) -> str:
    fastest = min(options, key=lambda option: option.hours)
    if len(options) == 1:
        reason = "OSRM returned a single route."
    elif not fastest.feasible:
        reason = "The fastest route has no feasible fuel plan; chose the feasible route with the lowest total operating cost."
    elif chosen is fastest:
        reason = "The fastest route has the lowest total operating cost, including fuel, driving time and stops."
    else:
        saved = fastest.trip_fuel_cost - chosen.trip_fuel_cost
        extra_cost = chosen.driving_cost - fastest.driving_cost
        extra = (chosen.hours - fastest.hours) * 60
        reason = (
            f"Alternative saves ${fastest.score - chosen.score:.2f} in total operating cost: "
            f"${saved:.2f} less fuel cost, ${extra_cost:.2f} extra driving cost "
            f"({extra:.0f} minutes), and ${fastest.stop_cost - chosen.stop_cost:.2f} less stop cost."
        )
    return reason


def _summary(chosen: RouteOption) -> dict:
    costs = chosen.costs
    if costs is None or chosen.purchases is None:
        raise ValueError("Cannot present an infeasible route as the chosen plan.")
    return {
        "number_of_stops": len(chosen.purchases),
        "total_fuel_cost": round(costs.fuel_purchases_cost, 2),
        "total_gallons_purchased": round(costs.gallons_purchased, 2),
        "total_gallons_used": round(costs.gallons_used, 2),
        "gallons_used_from_start_tank": round(costs.gallons_used_from_start_tank, 2),
        "start_tank_fuel_value": round(costs.start_tank_fuel_value, 2),
        "estimated_trip_fuel_cost": round(costs.trip_fuel_cost, 2),
        "estimated_driving_cost": round(costs.driving_cost, 2),
        "estimated_stop_cost": round(costs.stop_cost, 2),
        "estimated_total_operating_cost": round(costs.total_operating_cost, 2),
        "average_price_paid": round(costs.average_price_paid, 3)
        if costs.average_price_paid is not None
        else None,
    }


def build_response(
    start: Location,
    finish: Location,
    chosen: RouteOption,
    options: list[RouteOption],
    stations: StationIndex,
    config: PlanningConfig,
    start_gallons: float,
    start_price: float,
    start_price_source: str,
    routes_cached: bool,
) -> dict:
    if chosen.purchases is None:
        raise ValueError("Cannot present an infeasible route as the chosen plan.")
    route, purchases = chosen.route, chosen.purchases
    reason = describe_selection(chosen, options)
    stops = []
    for number, purchase in enumerate(purchases, start=1):
        match_position = purchase.candidate.key
        station_position = chosen.matches.station_positions[match_position]
        stops.append(
            {
                "stop_number": number,
                "opis_id": int(stations.ids[station_position]),
                "name": stations.names[station_position],
                "address": stations.addresses[station_position],
                "city": stations.cities[station_position],
                "state": stations.states[station_position],
                "latitude": round(float(stations.latitudes[station_position]), 5),
                "longitude": round(float(stations.longitudes[station_position]), 5),
                "price_per_gallon": round(float(stations.prices[station_position]), 3),
                "mile_marker": round(purchase.candidate.mile, 1),
                "distance_from_route_miles": round(
                    float(chosen.matches.distances_from_route[match_position]), 1
                ),
                "fuel_on_arrival_gallons": round(purchase.fuel_on_arrival_gallons, 2),
                "gallons_purchased": round(purchase.gallons, 2),
                "cost": round(purchase.cost, 2),
            }
        )

    fastest = min(options, key=lambda option: option.hours)
    return {
        "start": start.as_dict(),
        "finish": finish.as_dict(),
        "route": {
            "route_index": chosen.index,
            "distance_miles": round(route.distance_miles, 1),
            "duration_hours": round(chosen.hours, 2),
            "stations_considered": len(chosen.matches),
        },
        "route_selection": {
            "rule": (
                f"Lowest whole-trip fuel cost + driving hours x ${config.costs.driver_truck_cost_per_hour:g}/hour "
                f"+ ${config.costs.stop_penalty_usd:g}/stop among feasible routes."
            ),
            "reason": reason,
            "routes_compared": len(options),
            "options": [
                {
                    "route_index": option.index,
                    "distance_miles": round(option.route.distance_miles, 1),
                    "duration_hours": round(option.hours, 2),
                    "extra_minutes_vs_fastest": round(
                        (option.hours - fastest.hours) * 60
                    ),
                    "fuel_cost": round(option.fuel_cost, 2)
                    if option.feasible
                    else None,
                    "estimated_trip_fuel_cost": round(option.trip_fuel_cost, 2)
                    if option.feasible
                    else None,
                    "driving_cost": round(option.driving_cost, 2),
                    "stop_cost": round(option.stop_cost, 2)
                    if option.feasible
                    else None,
                    "total_operating_cost": round(option.score, 2)
                    if option.feasible
                    else None,
                    "number_of_stops": len(option.purchases)
                    if option.feasible
                    else None,
                    "error": option.error,
                    "selected": option is chosen,
                }
                for option in options
            ],
        },
        "vehicle": {
            "range_miles": config.vehicle.range_miles,
            "miles_per_gallon": config.vehicle.mpg,
            "tank_gallons": config.vehicle.tank_gallons,
            "start_fuel_gallons": round(start_gallons, 2),
            "start_fuel_price_per_gallon": start_price,
            "start_fuel_price_source": start_price_source,
            "driver_truck_cost_per_hour": config.costs.driver_truck_cost_per_hour,
            "assumption": "Fuel is bought only at stations on the route; the vehicle arrives with as little fuel as possible.",
        },
        "fuel_stops": stops,
        "summary": _summary(chosen),
        "map": _feature_collection(
            start,
            finish,
            chosen,
            options,
            stops,
            config.response_geometry_spacing_miles,
        ),
        "meta": {
            "routing_api_calls": 0 if routes_cached else 1,
            "route_from_cache": routes_cached,
        },
    }


def _line(route: Route, spacing_miles: float):
    lats, lons, _ = resample_polyline(route.latitudes, route.longitudes, spacing_miles)
    return [
        [round(float(lon), 5), round(float(lat), 5)] for lat, lon in zip(lats, lons)
    ]


def _feature_collection(
    start: Location,
    finish: Location,
    chosen: RouteOption,
    options,
    stops,
    response_spacing_miles: float,
) -> dict:
    """GeoJSON that renders as-is on geojson.io, Leaflet, Mapbox, QGIS..."""

    def line(coords, properties):
        return {
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coords},
            "properties": properties,
        }

    def point(lon, lat, properties):
        return {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": properties,
        }

    alternatives = [
        line(
            _line(option.route, ALTERNATIVE_GEOMETRY_SPACING_MILES),
            {
                "kind": "alternative",
                "route_index": option.index,
                "label": f"Alternative {option.index}: {option.route.distance_miles:.0f} mi, {option.hours:.1f} h"
                + (
                    f", fuel ${option.fuel_cost:.2f}"
                    if option.feasible
                    else ", no feasible fuel plan"
                ),
                "stroke": "#9e9e9e",
            },
        )
        for option in options
        if option is not chosen
    ]
    chosen_line = line(
        _line(chosen.route, response_spacing_miles),
        {"kind": "route", "route_index": chosen.index, "stroke": "#1e88e5"},
    )
    return {
        "type": "FeatureCollection",
        "features": [
            *alternatives,
            chosen_line,
            point(
                start.longitude,
                start.latitude,
                {"kind": "start", "label": start.label, "marker-color": "#2e7d32"},
            ),
            point(
                finish.longitude,
                finish.latitude,
                {"kind": "finish", "label": finish.label, "marker-color": "#c62828"},
            ),
            *(
                point(
                    s["longitude"],
                    s["latitude"],
                    {
                        "kind": "fuel_stop",
                        "stop_number": s["stop_number"],
                        "label": f"#{s['stop_number']} {s['name']} - ${s['price_per_gallon']}/gal",
                        "gallons_purchased": s["gallons_purchased"],
                        "cost": s["cost"],
                        "marker-color": "#1565c0",
                        "marker-symbol": "fuel",
                    },
                )
                for s in stops
            ),
        ],
    }

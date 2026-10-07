"""Read Django settings at the application boundary, then pass snapshots."""

from dataclasses import dataclass
from math import isfinite

from django.conf import settings

from .costs import CostConfig, VehicleConfig


@dataclass(frozen=True)
class PlanningConfig:
    vehicle: VehicleConfig
    costs: CostConfig
    max_station_distance_miles: float
    route_sample_spacing_miles: float
    response_geometry_spacing_miles: float

    def __post_init__(self):
        if (
            not isfinite(self.max_station_distance_miles)
            or self.max_station_distance_miles < 0
        ):
            raise ValueError("Station distance must be a nonnegative finite number.")
        spacings = (
            self.route_sample_spacing_miles,
            self.response_geometry_spacing_miles,
        )
        if not all(isfinite(value) and value > 0 for value in spacings):
            raise ValueError("Route sample spacings must be positive finite numbers.")


def get_vehicle_config() -> VehicleConfig:
    config = settings.FUEL_ROUTE
    return VehicleConfig(config["VEHICLE_RANGE_MILES"], config["VEHICLE_MPG"])


def get_planning_config() -> PlanningConfig:
    config = dict(settings.FUEL_ROUTE)
    return PlanningConfig(
        vehicle=VehicleConfig(config["VEHICLE_RANGE_MILES"], config["VEHICLE_MPG"]),
        costs=CostConfig(
            config["DRIVER_TRUCK_COST_PER_HOUR"], config["STOP_PENALTY_USD"]
        ),
        max_station_distance_miles=config["MAX_STATION_DISTANCE_FROM_ROUTE_MILES"],
        route_sample_spacing_miles=config["ROUTE_SAMPLE_SPACING_MILES"],
        response_geometry_spacing_miles=config["RESPONSE_GEOMETRY_SPACING_MILES"],
    )

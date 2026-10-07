"""Pure vehicle and trip-cost calculations; no framework or I/O dependencies."""

from dataclasses import dataclass
from math import isfinite
from typing import Sequence

from .optimizer import Purchase


@dataclass(frozen=True)
class VehicleConfig:
    range_miles: float
    mpg: float

    def __post_init__(self):
        if not all(
            isfinite(value) and value > 0 for value in (self.range_miles, self.mpg)
        ):
            raise ValueError("Vehicle range and MPG must be positive finite numbers.")

    @property
    def tank_gallons(self) -> float:
        return self.range_miles / self.mpg


@dataclass(frozen=True)
class CostConfig:
    driver_truck_cost_per_hour: float
    stop_penalty_usd: float

    def __post_init__(self):
        values = (self.driver_truck_cost_per_hour, self.stop_penalty_usd)
        if not all(isfinite(value) and value >= 0 for value in values):
            raise ValueError(
                "Hourly and stop costs must be nonnegative finite numbers."
            )


@dataclass(frozen=True)
class CostBreakdown:
    fuel_purchases_cost: float
    gallons_purchased: float
    gallons_used: float
    gallons_used_from_start_tank: float
    start_tank_fuel_value: float
    driving_cost: float
    stop_cost: float

    @property
    def trip_fuel_cost(self) -> float:
        return self.fuel_purchases_cost + self.start_tank_fuel_value

    @property
    def total_operating_cost(self) -> float:
        return self.trip_fuel_cost + self.driving_cost + self.stop_cost

    @property
    def average_price_paid(self) -> float | None:
        return (
            self.fuel_purchases_cost / self.gallons_purchased
            if self.gallons_purchased
            else None
        )


def driving_cost(duration_seconds: float, config: CostConfig) -> float:
    return duration_seconds / 3600 * config.driver_truck_cost_per_hour


def calculate_costs(
    purchases: Sequence[Purchase],
    distance_miles: float,
    duration_seconds: float,
    start_fuel_price_per_gallon: float,
    vehicle: VehicleConfig,
    config: CostConfig,
) -> CostBreakdown:
    gallons_purchased = sum(purchase.gallons for purchase in purchases)
    gallons_used = distance_miles / vehicle.mpg
    used_from_start = max(gallons_used - gallons_purchased, 0.0)
    return CostBreakdown(
        fuel_purchases_cost=sum(purchase.cost for purchase in purchases),
        gallons_purchased=gallons_purchased,
        gallons_used=gallons_used,
        gallons_used_from_start_tank=used_from_start,
        start_tank_fuel_value=used_from_start * start_fuel_price_per_gallon,
        driving_cost=driving_cost(duration_seconds, config),
        stop_cost=config.stop_penalty_usd * len(purchases),
    )

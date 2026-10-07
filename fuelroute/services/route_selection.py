"""Framework-independent route results and operating-cost selection."""

from __future__ import annotations

from dataclasses import dataclass
from math import inf
from typing import TYPE_CHECKING

from .costs import CostBreakdown, CostConfig, driving_cost
from .optimizer import InfeasibleRouteError, Purchase
from .station_matching import StationMatches

if TYPE_CHECKING:
    from .routing import Route


@dataclass(frozen=True)
class RouteOption:
    index: int
    route: Route
    matches: StationMatches
    purchases: tuple[Purchase, ...] | None
    costs: CostBreakdown | None
    cost_config: CostConfig
    error: str | None = None

    def __post_init__(self):
        if (self.purchases is None) != (self.costs is None):
            raise ValueError("A feasible route must have both purchases and costs.")
        if self.purchases is not None and self.error is not None:
            raise ValueError("A feasible route cannot also contain an error.")

    @property
    def feasible(self) -> bool:
        return self.costs is not None

    @property
    def fuel_cost(self) -> float | None:
        return self.costs.fuel_purchases_cost if self.costs is not None else None

    @property
    def trip_fuel_cost(self) -> float | None:
        return self.costs.trip_fuel_cost if self.costs is not None else None

    @property
    def driving_cost(self) -> float:
        if self.costs is not None:
            return self.costs.driving_cost
        return driving_cost(self.route.duration_seconds, self.cost_config)

    @property
    def stop_cost(self) -> float | None:
        return self.costs.stop_cost if self.costs is not None else None

    @property
    def score(self) -> float:
        """Infeasible plans rank as infinity; no arithmetic on missing costs."""
        return self.costs.total_operating_cost if self.costs is not None else inf

    @property
    def hours(self) -> float:
        return self.route.duration_seconds / 3600


def choose_route(options: list[RouteOption]) -> RouteOption:
    """Choose the feasible minimum, preferring faster roads when costs tie."""
    if not options:
        raise InfeasibleRouteError("No candidate roads available for this trip.")
    feasible = [option for option in options if option.feasible]
    if not feasible:
        fastest = min(options, key=lambda option: option.hours)
        raise InfeasibleRouteError(
            fastest.error or "No feasible refuelling plan for this route."
        )
    return min(feasible, key=lambda option: (round(option.score, 6), option.hours))

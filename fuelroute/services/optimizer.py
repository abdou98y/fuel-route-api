"""Cheapest refuelling plan along a fixed route.

Minimises ``fuel cost + stop_penalty * number_of_stops``. The penalty is a
small dollar value standing in for the time a stop costs; it stops the plan
from pulling over twice in 10 miles to save a few cents. With a penalty of 0
this is the exact minimum-cost plan.

Algorithm (Khuller, Malekian & Mestre, "To Fill or Not to Fill"): for a fixed
set of stops, an optimal driver at stop ``i`` heading to stop ``j`` either
* fills the tank, if ``j`` is more expensive than ``i``, or
* buys just enough to reach ``j`` (arriving empty), if ``j`` is cheaper or equal.

So the fuel left on arrival at ``j`` is either 0 or ``range - d(i, j)``, a small
set of values, and a DP over (station, fuel on arrival) is exact. Each station
reduces its states to a prefix-minimum array, so every transition is a binary
search: roughly O(n * k * log k) for n stations and k stations per tank.

The vehicle leaves the origin with ``start_fuel_miles`` of fuel (a full tank by
default) and can't buy at the origin. Trips requiring purchases finish empty;
shorter trips can retain unused starting fuel.
"""

from bisect import bisect_right
from dataclasses import dataclass
from math import inf

EPSILON = 1e-9


class InfeasibleRouteError(Exception):
    pass


@dataclass(frozen=True)
class Candidate:
    """A station somewhere along the route."""

    key: int  # caller's identifier (index into the station index)
    mile: float  # distance from the origin along the route
    price: float  # USD per gallon


@dataclass(frozen=True)
class Purchase:
    candidate: Candidate
    gallons: float
    cost: float
    fuel_on_arrival_gallons: float


@dataclass
class _State:
    cost: float
    prev: tuple | None  # (node, fuel on arrival there, miles of fuel bought there)


def plan_fuel_stops(
    candidates: list[Candidate],
    total_miles: float,
    range_miles: float,
    mpg: float,
    start_fuel_miles: float | None = None,
    stop_penalty: float = 0.0,
) -> list[Purchase]:
    start_fuel = (
        range_miles if start_fuel_miles is None else min(start_fuel_miles, range_miles)
    )
    # DP fuel quantities are miles rather than gallons, so its monetary values
    # are scaled by MPG. Scale the dollar stop penalty by the same factor.
    scaled_stop_penalty = stop_penalty * mpg
    stations = _dedupe(c for c in candidates if 0 < c.mile < total_miles)

    # Nodes: 0 = origin, 1..n = stations, n + 1 = destination.
    miles = [0.0, *(s.mile for s in stations), total_miles]
    prices = [inf, *(s.price for s in stations), 0.0]
    dest = len(miles) - 1
    states: list[dict[float, _State]] = [{} for _ in miles]
    states[0][start_fuel] = _State(0.0, None)

    def relax(node, fuel, cost, prev):
        fuel = round(fuel, 6)
        current = states[node].get(fuel)
        if current is None or cost < current.cost - EPSILON:
            states[node][fuel] = _State(cost, prev)

    for i in range(dest):
        if not states[i]:
            continue
        if i == 0:
            # Can't buy at the origin: just drive to anything the tank reaches.
            for j in range(1, dest + 1):
                d = miles[j]
                if d > start_fuel + EPSILON:
                    break
                relax(
                    j,
                    start_fuel - d,
                    0.0 if j == dest else scaled_stop_penalty,
                    (0, start_fuel, 0.0),
                )
            continue

        # Sorted by fuel on arrival, with prefix-minimum of (cost - fuel * price):
        # the cost of buying up to a level X is (X - g) * price + cost(g).
        price = prices[i]
        fuels = sorted(states[i])
        best_val, best_fuel = [], []
        for g in fuels:
            val = states[i][g].cost - g * price
            if not best_val or val < best_val[-1]:
                best_val.append(val), best_fuel.append(g)
            else:
                best_val.append(best_val[-1]), best_fuel.append(best_fuel[-1])

        for j in range(i + 1, dest + 1):
            d = miles[j] - miles[i]
            if d > range_miles + EPSILON:
                break
            penalty = 0.0 if j == dest else scaled_stop_penalty
            if prices[j] <= price:
                # Buy just enough to reach j, using the best arrival state with g <= d.
                k = bisect_right(fuels, d + EPSILON) - 1
                if k < 0:
                    continue
                g = best_fuel[k]
                relax(j, 0.0, best_val[k] + d * price + penalty, (i, g, d - g))
            else:
                # j is pricier: fill the tank here.
                g = best_fuel[-1]
                relax(
                    j,
                    range_miles - d,
                    best_val[-1] + range_miles * price + penalty,
                    (i, g, range_miles - g),
                )

    if not states[dest]:
        raise InfeasibleRouteError(_gap_message(miles, start_fuel, range_miles))

    return _reconstruct(states, stations, dest, mpg)


def _dedupe(candidates):
    """Sort by mile; at (almost) the same spot only the cheapest station matters."""
    result: list[Candidate] = []
    for c in sorted(candidates, key=lambda c: (round(c.mile, 3), c.price)):
        if result and abs(result[-1].mile - c.mile) < 1e-3:
            continue
        result.append(c)
    return result


def _reconstruct(states, stations, dest, mpg):
    purchases = []
    node, fuel = dest, min(states[dest], key=lambda g: states[dest][g].cost)
    while (prev := states[node][fuel].prev) is not None:
        node, fuel, bought = prev
        if node > 0 and bought > EPSILON:
            station = stations[node - 1]
            gallons = bought / mpg
            purchases.append(
                Purchase(station, gallons, gallons * station.price, fuel / mpg)
            )
    purchases.reverse()
    return purchases


def _gap_message(miles, start_fuel, range_miles):
    reach = start_fuel
    for a, b in zip(miles, miles[1:]):
        if b > reach + EPSILON and a == 0 and start_fuel < range_miles:
            return (
                f"Starting fuel only covers {start_fuel:.0f} miles, but the first fuel station "
                f"(or the destination) is at mile {b:.0f}."
            )
        if b > reach + EPSILON:
            return f"No fuel station within {range_miles:.0f} miles after mile {a:.0f} of the route."
        reach = b + range_miles
    return "No feasible refuelling plan for this route."

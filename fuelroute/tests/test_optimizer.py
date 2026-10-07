import random

import numpy as np
import pytest
from scipy.optimize import linprog

from fuelroute.services.optimizer import (
    Candidate,
    InfeasibleRouteError,
    plan_fuel_stops,
)

RANGE, MPG = 500.0, 10.0


def lp_min_cost(stations, total, range_miles=RANGE, start_fuel=RANGE):
    """Reference optimum via linear programming (stops are free).

    x_i = miles of fuel bought at station i. Fuel on arrival at every station
    and at the destination must be >= 0, and after buying it must be <= tank.
    """
    stations = sorted(stations, key=lambda s: s.mile)
    n = len(stations)
    if n == 0:
        return 0.0 if total <= start_fuel else None
    a_ub, b_ub = [], []
    for k, s in enumerate(stations):
        # arrival fuel at k = start + sum(x_<k) - mile_k >= 0
        a_ub.append([-1.0 if i < k else 0.0 for i in range(n)])
        b_ub.append(start_fuel - s.mile)
        # after buying at k: start + sum(x_<=k) - mile_k <= tank
        a_ub.append([1.0 if i <= k else 0.0 for i in range(n)])
        b_ub.append(range_miles - start_fuel + s.mile)
    a_ub.append([-1.0] * n)  # reach the destination
    b_ub.append(start_fuel - total)
    res = linprog(
        [s.price / MPG for s in stations],
        A_ub=a_ub,
        b_ub=b_ub,
        bounds=(0, None),
        method="highs",
    )
    return res.fun if res.success else None


def total_cost(purchases):
    return sum(p.cost for p in purchases)


def test_no_stop_needed_when_trip_fits_in_tank():
    stations = [Candidate(0, 100, 3.0), Candidate(1, 200, 2.0)]
    assert plan_fuel_stops(stations, 450, RANGE, MPG) == []


def test_buys_only_what_is_needed_to_finish():
    purchases = plan_fuel_stops([Candidate(0, 400, 3.0)], 700, RANGE, MPG)
    assert len(purchases) == 1
    # Arrives with 100 miles left, needs 300 more -> buys 200 miles = 20 gal.
    assert purchases[0].gallons == pytest.approx(20)
    assert purchases[0].cost == pytest.approx(60)


def test_prefers_cheaper_station_further_ahead():
    stations = [Candidate(0, 300, 4.0), Candidate(1, 450, 3.0)]
    purchases = plan_fuel_stops(stations, 900, RANGE, MPG)
    assert [p.candidate.key for p in purchases] == [1]
    assert purchases[0].gallons == pytest.approx(40)


def test_fills_up_before_expensive_stretch():
    stations = [Candidate(0, 400, 2.0), Candidate(1, 800, 5.0)]
    purchases = plan_fuel_stops(stations, 1100, RANGE, MPG)
    assert purchases[0].candidate.key == 0 and purchases[0].gallons == pytest.approx(
        40
    )  # full tank
    assert purchases[1].candidate.key == 1 and purchases[1].gallons == pytest.approx(20)


def test_stop_penalty_avoids_micro_stops():
    # Strictly cheapest: fill at A, then top up 1 gal at B ten miles later, saving $0.45.
    stations = [Candidate(0, 450, 2.99), Candidate(1, 460, 3.00)]
    strict = plan_fuel_stops(stations, 960, RANGE, MPG)
    relaxed = plan_fuel_stops(stations, 960, RANGE, MPG, stop_penalty=5)
    assert [p.candidate.key for p in strict] == [0, 1]
    assert [p.candidate.key for p in relaxed] == [1]
    assert total_cost(relaxed) - total_cost(strict) == pytest.approx(0.45)


def test_stop_penalty_is_in_dollars_not_scaled_down_by_mpg():
    stations = [Candidate(0, 450, 2.95), Candidate(1, 460, 3.00)]
    strict = plan_fuel_stops(stations, 960, RANGE, MPG)
    relaxed = plan_fuel_stops(stations, 960, RANGE, MPG, stop_penalty=5)
    assert len(strict) == 2 and len(relaxed) == 1
    assert total_cost(relaxed) - total_cost(strict) == pytest.approx(2.25)


@pytest.mark.parametrize("seed", range(10))
def test_penalty_matches_exhaustive_station_subsets_and_lp(seed):
    from itertools import combinations

    rng = random.Random(seed)
    total = rng.uniform(700, 1500)
    stations = [
        Candidate(i, rng.uniform(1, total - 1), rng.uniform(2, 4)) for i in range(5)
    ]
    possible = []
    for n in range(len(stations) + 1):
        for subset in combinations(stations, n):
            cost = lp_min_cost(sorted(subset, key=lambda c: c.mile), total)
            if cost is not None:
                possible.append(cost + 5 * n)
    if not possible:
        with pytest.raises(InfeasibleRouteError):
            plan_fuel_stops(stations, total, RANGE, MPG, stop_penalty=5)
    else:
        purchases = plan_fuel_stops(stations, total, RANGE, MPG, stop_penalty=5)
        assert total_cost(purchases) + 5 * len(purchases) == pytest.approx(
            min(possible), abs=1e-4
        )


def test_raises_when_gap_exceeds_range():
    with pytest.raises(InfeasibleRouteError, match="after mile 400"):
        plan_fuel_stops([Candidate(0, 400, 3.0)], 1000, RANGE, MPG)


@pytest.mark.parametrize("seed", range(200))
def test_matches_linear_programming_optimum(seed):
    rng = random.Random(seed)
    total = rng.uniform(100, 3000)
    stations = [
        Candidate(i, rng.uniform(1, total - 1), round(rng.uniform(2.5, 4.5), 3))
        for i in range(rng.randint(0, 60))
    ]
    expected = lp_min_cost(stations, total)
    if expected is None:
        with pytest.raises(InfeasibleRouteError):
            plan_fuel_stops(stations, total, RANGE, MPG)
        return
    purchases = plan_fuel_stops(stations, total, RANGE, MPG)
    assert total_cost(purchases) == pytest.approx(expected, abs=1e-4)
    # And the plan itself is physically valid.
    fuel, pos = RANGE, 0.0
    for p in purchases:
        fuel -= p.candidate.mile - pos
        assert fuel >= -1e-4
        assert p.fuel_on_arrival_gallons * MPG == pytest.approx(fuel, abs=1e-4)
        fuel += p.gallons * MPG
        assert fuel <= RANGE + 1e-4
        pos = p.candidate.mile
    assert fuel - (total - pos) == pytest.approx(0, abs=1e-4) or not purchases


def test_penalised_plan_is_never_much_more_expensive():
    rng = np.random.default_rng(7)
    for _ in range(50):
        miles = np.sort(rng.uniform(1, 2999, 150))
        stations = [
            Candidate(i, float(m), float(rng.uniform(2.8, 3.6)))
            for i, m in enumerate(miles)
        ]
        strict = plan_fuel_stops(stations, 3000, RANGE, MPG)
        relaxed = plan_fuel_stops(stations, 3000, RANGE, MPG, stop_penalty=5)
        assert len(relaxed) <= len(strict)
        assert (
            total_cost(relaxed) - total_cost(strict)
            <= 5 * (len(strict) - len(relaxed)) + 1e-6
        )

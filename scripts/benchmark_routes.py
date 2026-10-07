"""Reproducible OSRM sample and offline planner benchmark.

Run from the project root: .venv/bin/python scripts/benchmark_routes.py
Live requests are sequential, at least one second apart. Saved responses allow
offline repeat measurements: add --offline. No concurrent public API load test.
"""

import argparse
import gzip
import json
import os
import platform
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()
import requests
from django.conf import settings

from fuelroute.services.configuration import get_planning_config
from fuelroute.services.geocoding import Location, geocode
from fuelroute.services.optimizer import InfeasibleRouteError
from fuelroute.services.planner import build_plan, plan_route
from fuelroute.services.routing import route_from_payload
from fuelroute.services.station_index import get_station_index

TRIPS = [
    ("Charlotte, NC", "Los Angeles, CA"),
    ("Kansas City, MO", "Las Vegas, NV"),
    ("Chicago, IL", "Denver, CO"),
    ("Dallas, TX", "Houston, TX"),
    ("New York, NY", "Los Angeles, CA"),
    ("Chicago, IL", "Albuquerque, NM"),
    ("Oklahoma City, OK", "Boise, ID"),
    ("Salt Lake City, UT", "Jacksonville, FL"),
    ("Atlanta, GA", "Miami, FL"),
    ("Seattle, WA", "San Francisco, CA"),
    ("Boston, MA", "Washington, DC"),
    ("Phoenix, AZ", "San Diego, CA"),
    ("Memphis, TN", "Dallas, TX"),
    ("Minneapolis, MN", "Nashville, TN"),
    ("Portland, OR", "Denver, CO"),
    ("Indianapolis, IN", "New Orleans, LA"),
    ("Houston, TX", "San Antonio, TX"),
    ("Detroit, MI", "Pittsburgh, PA"),
    ("Baltimore, MD", "St. Louis, MO"),
    ("Tampa, FL", "Charlotte, NC"),
]
COUNTS = [3, 5, 10, 20]
OUT = ROOT / "docs" / "benchmarks"


def stats(values):
    values = sorted(values)
    return (
        {
            "mean": round(statistics.mean(values), 3),
            "median": round(statistics.median(values), 3),
            "p95": round(values[min(len(values) - 1, int(len(values) * 0.95))], 3),
            "max": round(max(values), 3),
        }
        if values
        else None
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--offline", action="store_true")
    parser.add_argument(
        "--extend",
        action="store_true",
        help="Reuse saved requests; additional trips only request 3 alternatives.",
    )
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    session = requests.Session()
    for trip_id, (start, finish) in enumerate(TRIPS):
        for count in COUNTS if trip_id < 8 else [3]:
            path = OUT / f"trip-{trip_id}-alternatives-{count}.json.gz"
            if args.offline or (args.extend and path.exists()):
                if not path.exists():
                    continue
                with gzip.open(path, "rt") as f:
                    saved = json.load(f)
            else:
                a, b = geocode(start), geocode(finish)
                begun = time.perf_counter()
                url = f"{settings.FUEL_ROUTE['OSRM_BASE_URL']}/route/v1/driving/{a.longitude},{a.latitude};{b.longitude},{b.latitude}"
                try:
                    response = session.get(
                        url,
                        params={
                            "alternatives": count,
                            "overview": "full",
                            "geometries": "geojson",
                            "steps": "false",
                        },
                        headers={"User-Agent": settings.FUEL_ROUTE["HTTP_USER_AGENT"]},
                        timeout=20,
                    )
                    saved = {
                        "status": response.status_code,
                        "payload": response.json(),
                        "bytes": len(response.content),
                        "osrm_ms": (time.perf_counter() - begun) * 1000,
                    }
                except (requests.RequestException, ValueError) as exc:
                    saved = {
                        "status": None,
                        "error": str(exc),
                        "payload": {},
                        "bytes": 0,
                        "osrm_ms": (time.perf_counter() - begun) * 1000,
                    }
                with gzip.open(path, "wt") as f:
                    json.dump(saved, f)
                time.sleep(max(0, 1.1 - (time.perf_counter() - begun)))
            row = {
                "trip": f"{start} -> {finish}",
                "requested_alternatives": count,
                **{k: saved[k] for k in ("status", "bytes", "osrm_ms")},
                "code": saved["payload"].get("code"),
                "routes_returned": len(saved["payload"].get("routes", [])),
                "planner_ms": [],
            }
            rows.append(row)
            print(
                json.dumps({k: v for k, v in row.items() if k != "planner_ms"}),
                flush=True,
            )
    # Warm station arrays before timing, and repeat actual route planning locally.
    stations = get_station_index()
    config = get_planning_config()
    routes_by_trip = {}
    for row in rows:
        trip_id = next(
            i for i, (a, b) in enumerate(TRIPS) if row["trip"] == f"{a} -> {b}"
        )
        with gzip.open(
            OUT
            / f"trip-{trip_id}-alternatives-{row['requested_alternatives']}.json.gz",
            "rt",
        ) as f:
            payload = json.load(f)["payload"]
        routes = [route_from_payload(r) for r in payload.get("routes", [])]
        if not routes:
            continue
        routes_by_trip[row["trip"]] = routes
        for r in routes:
            for _ in range(5):
                begun = time.perf_counter()
                plan_route(0, r, 500, stations=stations, config=config)
                row["planner_ms"].append((time.perf_counter() - begun) * 1000)
    pools = list(routes_by_trip.values())
    concurrency = []
    # CPU-only, warm data, one process with threads (GIL included). External I/O,
    # full response rendering, HTTP handling, and cold startup are not measured.
    for users in [1, 5, 10, 25, 50]:
        barrier = threading.Barrier(users)

        def job(i):
            barrier.wait()
            begun = time.perf_counter()
            routes = pools[i % len(pools)]
            for j, r in enumerate(routes):
                plan_route(j, r, 500, stations=stations, config=config)
            return (time.perf_counter() - begun) * 1000

        if pools:
            batches, times = [], []
            for _ in range(5):
                begun = time.perf_counter()
                with ThreadPoolExecutor(max_workers=users) as executor:
                    times.extend(executor.map(job, range(users)))
                batches.append(time.perf_counter() - begun)
            concurrency.append(
                {
                    "users": users,
                    "repetitions": 5,
                    "batch_ms": stats([t * 1000 for t in batches]),
                    "request_processing_ms": stats(times),
                    "requests_per_second": round(users / statistics.mean(batches), 2),
                }
            )
    synthetic = []
    # Explicitly synthetic repeated real route geometry; estimates CPU growth
    # beyond the public server's limit, not actual returned alternative counts.
    for count in [1, 2, 4, 6, 11, 21]:
        times = []
        for routes in pools:
            for _ in range(3):
                begun = time.perf_counter()
                for i in range(count):
                    plan_route(
                        i,
                        routes[i % len(routes)],
                        500,
                        stations=stations,
                        config=config,
                    )
                times.append((time.perf_counter() - begun) * 1000)
        synthetic.append({"total_routes": count, "processing_ms": stats(times)})
    summary = {
        "utc_date": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "machine": {
            "platform": platform.platform(),
            "cpu_count": os.cpu_count(),
            "python": platform.python_version(),
        },
        "rows": rows,
        "by_requested_alternatives": {},
        "warm_threaded_concurrency": concurrency,
        "synthetic_route_counts": synthetic,
    }
    for count in COUNTS:
        samples = [r for r in rows if r["requested_alternatives"] == count]
        ok = [r for r in samples if r["code"] == "Ok"]
        summary["by_requested_alternatives"][count] = {
            "requests": len(samples),
            "successful": len(ok),
            "routes_returned": stats([r["routes_returned"] for r in ok]),
            "osrm_ms": stats([r["osrm_ms"] for r in ok]),
            "response_bytes": stats([r["bytes"] for r in ok]),
            "planner_ms_per_route": stats([t for r in ok for t in r["planner_ms"]]),
        }
    (OUT / "results.json").write_text(json.dumps(summary, indent=2) + "\n")
    plans = []
    for i, (start, finish) in enumerate(TRIPS):
        path = OUT / f"trip-{i}-alternatives-3.json.gz"
        if not path.exists():
            continue
        with gzip.open(path, "rt") as f:
            routes = [
                route_from_payload(r) for r in json.load(f)["payload"].get("routes", [])
            ]
        if not routes:
            continue
        # Snapshot the plan on recorded routes without any external geocoding
        # or routing calls. Input locations only affect response/map labels.
        r = routes[0]
        locations = [
            Location(
                start,
                start,
                float(r.latitudes[0]),
                float(r.longitudes[0]),
                "recorded_route",
            ),
            Location(
                finish,
                finish,
                float(r.latitudes[-1]),
                float(r.longitudes[-1]),
                "recorded_route",
            ),
        ]
        try:
            with (
                patch(
                    "fuelroute.services.planner.get_routes", return_value=(routes, True)
                ),
                patch("fuelroute.services.planner.geocode", side_effect=locations),
            ):
                plan = build_plan(start, finish)
            plan.pop("map")
        except InfeasibleRouteError as exc:
            plan = {"start": start, "finish": finish, "error": str(exc)}
        plans.append(plan)
    (OUT / "plans.json").write_text(json.dumps(plans, indent=2) + "\n")
    print(
        json.dumps({k: v for k, v in summary.items() if k != "rows"}, indent=2),
        flush=True,
    )


if __name__ == "__main__":
    main()

# Route alternatives and capacity benchmark

Measured on 6 October 2026, on this development machine (20 logical CPUs, Python 3.12.3).

Live OSRM calls were sequential and spaced at least 1.1 seconds apart. The 20 routes are selected short and long US trips, not a representative random sample. Eight of those trips also requested higher counts: 44 live routing calls in total. Warm processing was then repeated offline using saved geometries and the imported station database.

## What the server actually returns

| Alternatives requested | Successful / calls | Mean total roads | Maximum total roads | Mean OSRM round-trip |
|---|---:|---:|---:|---:|
| 3 | 20/20 | 1.7 | 2 | 374.019 ms |
| 5 | 0/8 | — | — | — ms |
| 10 | 0/8 | — | — | — ms |
| 20 | 0/8 | — | — | — ms |

**All 24 requests above three alternatives returned HTTP 400 / `TooBig`, with the message “Requested number of alternatives is higher than current maximum (3)”.** Six trips returned one road and fourteen returned two: average 1.7 total roads / 0.7 alternatives. Alternatives are additional to the primary road, so the configured upper bound is four total roads, although only two were observed.

OSRM round-trip median 352.14 ms; maximum 855.319 ms. Mean response size 507.6 KB. These are single measurements per trip, include transfer and parsing, and can be affected by upstream caches and network variation. They do not measure the incremental OSRM search time of higher counts, since those requests were rejected.

## Local cost per road

Station matching + DP fuel planning: mean **4.853 ms/road**, median 3.535 ms, p95 13.081 ms, maximum 43.394 ms. Each actual returned road was timed five times after station data loaded. These checks exclude geocoding, route acquisition, response geometry, JSON encoding, HTTP handling, cache access and cold startup.

## Simultaneous users: local planner only

Five bursts at each level, with threads released together using a barrier. Each user processes the recorded routes for one trip; trips rotate within the burst. The single-user case repeatedly uses the first, long Charlotte → LA trip. This is one Python process with threads and shared warm station data, so the GIL and workload mix affect results. It is not a full API load test or a multi-process test.

| Users submitting together | Mean processing latency/user | p95 processing latency/user | Mean whole burst duration |
|---:|---:|---:|---:|
| 1 | 16.9 ms | 17.6 ms | 17.4 ms |
| 5 | 27.3 ms | 53.7 ms | 53.3 ms |
| 10 | 36.7 ms | 71.3 ms | 68.3 ms |
| 25 | 122.0 ms | 207.3 ms | 218.0 ms |
| 50 | 207.7 ms | 361.9 ms | 375.8 ms |

## If another server could supply more roads

**Synthetic CPU experiment:** repeat recorded real geometries until the desired road count is reached; three repetitions over every sampled trip. These are repeated geometries, not new alternatives or a test of an OSRM server with a higher limit. They show the approximate local cost of evaluating more roads, without upstream search or transfer costs.

| Alternatives + primary | Total roads | Mean local processing/trip | p95 local processing/trip |
|---:|---:|---:|---:|
| 0 + 1 | 1 | 3.9 ms | 11.2 ms |
| 1 + 1 | 2 | 8.3 ms | 31.1 ms |
| 3 + 1 | 4 | 16.5 ms | 63.0 ms |
| 5 + 1 | 6 | 24.5 ms | 93.8 ms |
| 10 + 1 | 11 | 45.5 ms | 175.1 ms |
| 20 + 1 | 21 | 58.1 ms | 225.4 ms |

## Small capacity calculation and decision

For a burst of **C requests** with **W independent planner workers**, a rough CPU-only completion budget is:

```text
burst planning time ≈ ceil(C / W) × planning time per request
planning time per request ≈ number of roads × measured time per road
```

As an illustration, four independent workers and the configured maximum of four total roads give the following estimate. Workers are assumed to have sufficient separate CPU capacity. This is arithmetic using the synthetic four-road measurement, not measured multi-process capacity; the p95 column uses a conservative per-request budget and is not a computed burst percentile.

| Simultaneous requests | CPU budget at mean four-road time | CPU budget at p95 four-road time |
|---:|---:|---:|
| 10 | 50 ms | 189 ms |
| 25 | 116 ms | 441 ms |
| 50 | 215 ms | 819 ms |
| 100 | 413 ms | 1576 ms |

Add geocoding, routing latency/queuing, response building and HTTP overhead to obtain full response time. A target user count, request rate, cache-hit rate and latency budget are still needed to size a production deployment. Do not assume the machine’s 20 logical CPUs are 20 effective workers.

**Recommendation:** keep `ROUTE_ALTERNATIVES=3`, the highest count accepted by the current public server, and evaluate every returned road. This uses one routing call and cheap local checks. Raising the setting to 5/10/20 cannot yield more roads on this server. If larger coverage or concurrent cold requests become a requirement, self-host/configure OSRM (or use another service), then benchmark both additional route coverage and full end-to-end latency.

The [published demo-server policy](https://routing.openstreetmap.de/about.html) permits **one request/second maximum** and prohibits heavy usage. This is the controlling limit for uncached trips, not the local optimizer. At that limit, 50 distinct cold trips need roughly 50 seconds to dispatch; 100 distinct cold trips roughly 100 seconds. A shared cache can reuse trips; production also needs coordinated upstream request limiting and request coalescing, which are not currently implemented. Do not send concurrent cold-load tests to the public demo.

## Cost-model results

Whole-trip fuel includes consumed starting fuel at one shared starting price (default: lowest imported station price, currently $2.68733/gal). Ranking adds $50/hour driving cost excluding fuel, plus the existing $5/stop allowance. The previous 10% time cutoff is removed.

Captured live routes replayed through the updated planner: Charlotte → LA and Kansas City → Las Vegas retain the main road; New York → LA selects the alternative, saving about $16.85 overall. Nineteen of twenty trips are feasible in the current station data. Seattle → San Francisco is infeasible because no station is found within 500 miles after mile 260. `plans.json` contains every outcome.

The stop-penalty units bug was corrected before these final local measurements. The pure-cost optimizer still matches LP on 200 random cases; the $5-penalised objective is now also checked against exhaustive station subsets plus LP on ten cases.

## Reproduce

From the project root after importing stations:

```bash
.venv/bin/python scripts/benchmark_routes.py --offline
# To deliberately collect a fresh small live sample:
.venv/bin/python scripts/benchmark_routes.py
```

`--offline` reuses `.json.gz` responses and makes no routing or geocoding calls. `--extend` reuses existing responses and collects only missing ones. Live reruns overwrite saved results. `results.json` stores raw timings, machine metadata and summaries; `plans.json` stores updated plans with response maps omitted. Raw replies are included so the evidence is retained in the project.

[OSRM alternatives semantics](https://project-osrm.org/docs/v5.24.0/api/) describe a requested upper bound, with no guarantee alternatives exist.

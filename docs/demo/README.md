# Postman demo

Prepared on 2026-10-07 for the local API at `http://127.0.0.1:8000`.

Import `postman_demo_collection.json` into Postman. All URLs, query parameters and the POST body are filled. Authentication and environments are not needed. Open requests in numbered order and click **Send**. Automated checks appear in **Test Results**.

Verification:

- Django upgraded from 6.1.1 to 6.1.2, the latest official release listed at https://www.djangoproject.com/download/ when checked.
- 313 offline code tests pass; Django system checks and pending-migration check pass.
- 11 Postman demo requests and 67 response assertions pass using Newman 6.2.1 against the running local server.
- Those collection-run timings used cached routing replies and are not cold external-service latency measurements.
- A separate fresh live NY-LA request succeeded with one routing call (971.1 ms planner time); its repeat used zero routing calls (45.7 ms). Full response: `first-live-call-response.json`.
- The HTML map response contains embedded GeoJSON and Leaflet setup. Browser rendering was not part of these HTTP checks. View it in a browser using the JSON response's `map_url`.

## Recorded collection results

| Request | Expected/actual HTTP status | Response time |
|---|---:|---:|
| 01. Plan route (GET) - New York to Los Angeles | 200 | 86 ms |
| 02. Repeat route (GET) - New York to Los Angeles, cache check | 200 | 53 ms |
| 03. Map (HTML) - New York to Los Angeles | 200 | 55 ms |
| 04. Compare total operating cost - Charlotte to Los Angeles | 200 | 32 ms |
| 05. Plan route (POST) - Chicago to Denver, 25 gal at start | 200 | 34 ms |
| 06. Short trip - no stop needed | 200 | 10 ms |
| 07. Short trip - starting fuel valued at $3.25 per gallon | 200 | 9 ms |
| 08. Error - outside USA (London) | 400 | 9 ms |
| 09. Error - not enough starting fuel (0 gal) | 422 | 11 ms |
| 10. Error - start fuel above tank size (60 gal) | 400 | 8 ms |
| 11. Error - negative starting fuel price | 400 | 8 ms |

Full machine-readable evidence: `newman-results.json` and `newman-results.xml`.

To repeat the checks:

```bash
npm exec --yes --package=newman@6.2.1 -- newman run postman_demo_collection.json --delay-request 1100
```

For a five-minute recording, show 01/02 (long route and cache), 03 (map), 04 (route choice), 05 (POST), 06/07 (starting fuel), and one error. Use remaining time to explain routing, station matching, the optimizer and route selection.

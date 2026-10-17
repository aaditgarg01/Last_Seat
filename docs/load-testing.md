# Measured load-test results

Measured on 17 October 2026 using Python 3.13, PostgreSQL 18.4 (Homebrew), macOS 26.6.2 on ARM64, four Uvicorn API processes, and an asynchronous HTTPX generator on the same machine. Compose targets PostgreSQL 16; the Docker runtime was unavailable locally, so container startup was not exercised. PostgreSQL 16 integration coverage is configured in CI but has not yet run on GitHub.

Each scenario used a reset database seeded with 500 seats. Users were registered before the timed phase. Timings include reservation requests only; registration, confirmation and final database checks are excluded. The hold TTL was 120 seconds. No expiry worker ran during the load phase; automatic expiry and worker payment processing were exercised by integration tests. Each winner was subsequently confirmed through the real HTTP endpoint.

| Scenario | Users | In-flight requests | Reserved | Conflicts | Confirmed | Transport errors |
|---|---:|---:|---:|---:|---:|---:|
| Distributed over 500 seats | 10,000 | 200 | 500 | 9,500 | 500 | 0 |
| All requesting seat 1 | 1,000 | 100 | 1 | 999 | 1 | 0 |

| Scenario | Duration | Requests/sec | p50 | p95 | Maximum |
|---|---:|---:|---:|---:|---:|
| 500 seats | 27.433 s | 364.53 | 360.56 ms | 1,651.38 ms | 5,895.26 ms |
| Single seat | 2.759 s | 362.46 | 183.41 ms | 742.37 ms | 2,142.10 ms |

Raw reports: [500 seats](load-results.json), [single seat](hotseat-results.json). The generator exits nonzero if expected winners, unique seat IDs, confirmations or response totals differ, or transport errors occur. Expected `409` contention responses are counted separately from server failures.

A direct PostgreSQL check after the 10,000-user scenario found 500 reservations, all confirmed, 500 distinct seats, 500 succeeded payments, and **zero duplicate active seats**. See [database invariants](database-invariants.json).

These runs demonstrate booking consistency under contention; they do not establish production capacity, performance at 10,000 simultaneous connections, distributed fairness or behavior during infrastructure failures. For deployment sizing, use a separate load-generator host, production-like hardware, sustained/burst profiles, worker restart tests, network failure injection and database telemetry.

The final integration suite passed **14 tests** in 1.05 seconds against PostgreSQL. The earlier cold run also passed all 14 in 6.31 seconds. One third-party deprecation warning concerns Starlette's HTTPX test adapter; there were no test failures. Python compilation, Compose configuration validation and whitespace checks passed. Docker image build and container execution remain unverified locally.

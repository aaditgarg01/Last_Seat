# The Last Seat

A REST backend for 500 concert seats and competing student bookings. Built with Python 3.13, FastAPI and PostgreSQL. PostgreSQL row locks, a partial unique index, durable payment jobs and idempotency records protect every booking transition across multiple API processes.

## Start with Docker

```sh
cp .env.example .env
# Choose an opening time and password in .env before first startup.
docker compose up --build -d
curl http://localhost:8000/health
```

Interactive API documentation: http://localhost:8000/docs. Machine-readable specification: http://localhost:8000/openapi.json. Compose initializes the schema and exactly 500 seats before starting the API and expiry/payment worker. The default opening date is in the past for immediate demonstration. Set `BOOKING_OPENS_AT` to the intended 6 PM with an explicit UTC offset, for example `2026-10-20T18:00:00+05:30`. This seed setting applies only to a new database; an existing event must be updated deliberately in PostgreSQL. Holds last 120 seconds by default.

```sh
docker compose logs -f api worker
docker compose down                    # retain bookings
# DESTRUCTIVE: use only for a fresh demo/load-test database
docker compose down -v
```

Docker configuration is provided; this implementation was verified with native PostgreSQL because Docker Desktop was not running in the development environment. No public deployment has been made.

## Example booking

Requires `curl` and `jq`. Store the returned bearer token securely; only its hash is stored in PostgreSQL. Registration is a demo account flow, without college identity verification.

```sh
BASE=http://localhost:8000
TOKEN=$(curl -fsS "$BASE/users" -H 'Content-Type: application/json' \
  -d '{"name":"Aadit"}' | jq -r .token)
curl -fsS "$BASE/seats"
RID=$(curl -fsS "$BASE/reservations" \
  -H "Authorization: Bearer $TOKEN" -H 'Idempotency-Key: reserve-001' \
  -H 'Content-Type: application/json' -d '{"seat_id":1}' | jq -r .id)
curl -fsS "$BASE/reservations/$RID/confirm" \
  -H "Authorization: Bearer $TOKEN" -H 'Idempotency-Key: pay-001' \
  -H 'Content-Type: application/json' -d '{"outcome":"success","delay_seconds":0}'
curl -fsS "$BASE/reservations/$RID" -H "Authorization: Bearer $TOKEN"
curl -fsS "$BASE/bookings?limit=50&offset=0" -H "Authorization: Bearer $TOKEN"
curl -fsS "$BASE/reservations/$RID/cancel" \
  -H "Authorization: Bearer $TOKEN" -H 'Idempotency-Key: cancel-001'
```

For a failed payment, reserve another seat and confirm with `{"outcome":"failure"}`. For delay, use `{"outcome":"success","delay_seconds":30}`; the API returns `202` and the worker settles it later. A delay beyond the hold deadline produces an expired reservation and voided payment. Poll status for current state. Payment outcomes are explicitly selected for reproducible simulation; there is no real payment provider or money transfer.

## Endpoints

| Method | Path | Purpose | Successful status |
|---|---|---|---|
| GET | `/health` | Database readiness | 200 |
| POST | `/users` | Register `{name}`, receive `{id,token}` | 201 |
| GET | `/events` | Event and opening timestamp | 200 |
| GET | `/seats` | All 500 seats: available/reserved/booked | 200 |
| POST | `/reservations` | Hold `{seat_id}` | 201 |
| POST | `/reservations/{id}/confirm` | Start one simulated payment, `{outcome,delay_seconds}` | 200 or 202 |
| POST | `/reservations/{id}/cancel` | Cancel a hold/booking; refund successful simulation | 200 |
| GET | `/reservations/{id}` | Owned reservation and payment status | 200 |
| GET | `/bookings` | Owned history, `limit` 1–200 and `offset` | 200 |

All reservation and booking endpoints require `Authorization: Bearer <token>`. POST reservation/confirm/cancel also require `Idempotency-Key` (1–128 characters). Keys are scoped to the user across operations. Reusing a key with a different path or payload returns `409`; exact retries replay the original status and response even after state changes. Use GET status to retrieve the current state. Application failures such as unavailable seats are replayed too; a later independent attempt needs a new key. Registration does not support idempotency.

Error codes: `401` invalid/missing identity, `403` before opening, `404` missing or another user's reservation, `409` occupied seat/inactive hold/key conflict, `422` invalid input or missing key, `503` temporary database failure. After transport failures or `503`, retry with the **same** key. No reservation extends its expiry when retried. Each reservation can have only one simulated payment, including requests with different keys. A failed payment ends the hold; reserve again to retry.

Availability is a snapshot, not a guarantee. A reserve request is authoritative. When all seats are held/booked, requests return `409`; cancelled/expired seats become reservable again. A waiting queue, Redis, Kafka and reverse proxy are not included.

## Local development and testing

Use a dedicated PostgreSQL database. SQLite is intentionally unsupported: these tests exercise PostgreSQL locking and indexes.

```sh
python3.13 -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
export DATABASE_URL='postgresql+psycopg://lastseat:lastseat@localhost:5432/lastseat'
python -m app.init_db
uvicorn app.main:app --workers 4
# In a second terminal with the same environment:
python -m app.worker
```

`requirements-lock.txt` records the full tested dependency set; install it for an identical Python 3.13 environment. `DATABASE_URL`, `RESERVATION_TTL_SECONDS`, `DB_POOL_SIZE` (10) and `DB_MAX_OVERFLOW` (5) configure runtime behavior. Initialize the database explicitly before launching services; schema creation is serialized with an advisory lock. The initial schema script is rerunnable, but is not a migration system for future schema changes.

Tests **truncate application tables**. Point at a separate empty test database:

```sh
DATABASE_URL='postgresql+psycopg://lastseat:lastseat@localhost:5432/lastseat_test' \
  ALLOW_TEST_RESET=1 pytest -q
```

Tests cover competing users, concurrent identical retries, same-key conflicts, database uniqueness, expiry and reuse, worker recovery, successful/failed/delayed payments, duplicate charges, cancellation races, ownership, validation, history and opening time.

## Load testing

Start API processes against a fresh initialized demo database. Install development dependencies on the load-generator machine and run:

```sh
python load/run.py --users 10000 --concurrency 200 --output docs/load-results.json
# Reset the DEMO database before a separate single-seat contention test:
python load/run.py --users 1000 --concurrency 100 --hotseat --output docs/hotseat-results.json
```

The script registers distinct users, sends 10,000 reservation attempts with 200 requests in flight, then confirms winning holds. It fails unless exactly 500 different seats win and confirm, every other request returns `409`, and no transport errors occur. The hot-seat variant requires exactly one winner. The default hold must exceed the test duration. Do not use against a database containing real bookings.

See [measured results](docs/load-testing.md), [concurrency and scaling](docs/architecture.md), and [ERD and booking workflow diagrams](docs/diagrams.md).

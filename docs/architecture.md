# Concurrency, consistency and scaling

## Transaction boundaries

PostgreSQL is the sole authority. API processes carry no reservation state. Transactions use the default READ COMMITTED isolation level. Each mutation locks the persistent seat row with `SELECT ... FOR UPDATE` before reading the current reservation, including when no reservation exists. Two students competing for the last seat cannot both observe it as free while holding that lock. Availability reads do not lock seats; the reserve endpoint makes the final decision.

After acquiring the lock, code reads `clock_timestamp()` from PostgreSQL. Using transaction-start time would allow a queued confirmation to accept an expired hold. Expired holds are transitioned to `expired` before a new insert. A partial unique index on `seat_id WHERE status IN ('held','confirmed')` independently prohibits two active claims. Expired rows remain in history. Pending payments have a unique reservation foreign key, so each reservation can produce only one simulated charge.

All state transitions acquire locks in this order: user/key advisory lock (write endpoints only), seat row, then reservation/payment writes. History acquires seat rows in increasing order. The worker processes one seat per transaction. No code locks a payment first and subsequently waits for a seat. This avoids a lock-order cycle among these operations.

The idempotency response and business writes commit in the same transaction. A PostgreSQL transaction-scoped advisory lock serializes identical user/key requests across processes. A fingerprint includes the operation, reservation ID and normalized request body. Reusing the key with different inputs returns a conflict. An application error is stored after its nested savepoint rolls back. Database failures roll back the entire transaction; the same key can safely retry. Advisory hash collisions can serialize unrelated requests but cannot replay a different user's response because the table lookup uses the complete key and user.

## Expiry and simulated payments

A hold has an immutable deadline; starting or retrying payment never extends it. The durable worker polls due payments and expired holds every 0.5 seconds. It locks the seat and rechecks current state. Multiple worker replicas may find the same candidate; only the first valid transition applies. Worker restarts lose no queued payment because jobs live in PostgreSQL. Status/history reads and seat mutations also reconcile relevant due state.

Expiry takes precedence when reconciliation occurs after the deadline, even when the payment's scheduled due time was earlier. This conservative rule means a worker outage may void a payment that otherwise could have succeeded. The simulator never represents money captured externally. A future real payment integration must record provider events and reconcile/refund late captures; it must never restore an expired or reallocated booking.

Failed payments cancel the hold immediately and release the seat. Cancellation voids pending simulations and marks successful ones refunded in the same transaction. If confirmation races cancellation, the seat lock imposes a serial order: cancellation either prevents confirmation, or follows success and refunds it. Old payment attempts cannot confirm a newer user's claim.

Seat lists classify an expired hold as available immediately even if background cleanup is late. A reserve operation performs cleanup under its lock before creating a new claim. Thus worker failure does not block seat reuse. Historical terminal states and payment rows remain available to the owner.

## Capacity and operational limits

The API exposes 500 seats and supports many simultaneous attempts; HTTP concurrency and 10,000 open sockets are different capacity questions. The supplied load results test 10,000 distinct users at bounded concurrency on one machine, not 10,000 instantaneous connections. A single popular seat necessarily serializes competing writes, while distinct seats lock independently.

Scale API replicas behind an ingress with TLS, admission limits and bounded request queues. Budget database pools globally: four API processes at 10 + 5 connections each allow up to 60 API connections; worker and administration need headroom. Use PgBouncer and reduce per-process pools before adding many replicas. Monitor pool wait times, lock wait times, p95 latency, worker lag, expiry counts, payment failures, conflicts and retry rates. Add jittered retry/backoff for transient failures; never convert `409` into automatic retries with the same request key.

Availability may be cached briefly in Redis for heavy browsing, but a cache must never authorize a booking. For high demand, add an admission/waiting queue with signed tokens and explicit fairness policy. For many events, partition by event and use `(event_id, seat_number)` identities. The current seed and API deliberately support one concert. Use cursor pagination when history becomes large, and archive old reservation data. Retain idempotency records for a documented retry window before introducing cleanup; the current implementation retains them indefinitely.

For production, replace open registration with college SSO, add account/ticket limits and rate limiting, restrict simulator controls to a sandbox, use secret management and least-privilege database roles, add database migrations and backups, and integrate a real provider with authenticated webhooks. Introduce an outbox before publishing transaction events to Kafka or a queue. Public deployment, external payments, automatic real refunds, Redis, Kafka, a reverse proxy and a waiting queue are outside this submission.

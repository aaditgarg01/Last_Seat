CREATE TABLE IF NOT EXISTS users (
 id uuid PRIMARY KEY, name varchar(100) NOT NULL, token_hash text UNIQUE NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
 id integer PRIMARY KEY, name text NOT NULL, opens_at timestamptz NOT NULL
);
CREATE TABLE IF NOT EXISTS seats (
 id integer PRIMARY KEY, event_id integer NOT NULL REFERENCES events(id), label text NOT NULL,
 UNIQUE(event_id, label)
);
CREATE TABLE IF NOT EXISTS reservations (
 id uuid PRIMARY KEY, user_id uuid NOT NULL REFERENCES users(id), seat_id integer NOT NULL REFERENCES seats(id),
 status text NOT NULL CHECK (status IN ('held','confirmed','expired','cancelled')),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), expires_at timestamptz NOT NULL,
 confirmed_at timestamptz, cancelled_at timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_booking_per_seat ON reservations(seat_id) WHERE status IN ('held','confirmed');
CREATE INDEX IF NOT EXISTS reservation_history ON reservations(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS expiring_holds ON reservations(expires_at) WHERE status='held';
CREATE TABLE IF NOT EXISTS payments (
 id uuid PRIMARY KEY, reservation_id uuid UNIQUE NOT NULL REFERENCES reservations(id),
 status text NOT NULL CHECK(status IN ('pending','succeeded','failed','voided','refunded')),
 outcome text NOT NULL CHECK(outcome IN ('success','failure')),
 due_at timestamptz NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX IF NOT EXISTS due_payments ON payments(due_at) WHERE status='pending';
CREATE TABLE IF NOT EXISTS idempotency (
 user_id uuid NOT NULL REFERENCES users(id), key varchar(128) NOT NULL, fingerprint text NOT NULL,
 response jsonb NOT NULL, code integer NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(user_id,key)
);

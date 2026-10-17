import os
from pathlib import Path
from app.db import engine, run


def initialize():
    with engine.begin() as c:
        # Serialize initialization across processes; never reset existing event data.
        run(c, "SELECT pg_advisory_xact_lock(50010000)")
        for statement in Path(__file__).with_name('schema.sql').read_text().split(';'):
            if statement.strip():
                run(c, statement)
        run(c, "INSERT INTO events VALUES (1,'The Last Seat',CAST(:opens AS timestamptz)) ON CONFLICT DO NOTHING",
            opens=os.getenv('BOOKING_OPENS_AT', '2026-01-01T18:00:00+05:30'))
        run(c, "INSERT INTO seats SELECT n,1,'A-' || lpad(n::text,3,'0') FROM generate_series(1,500) n ON CONFLICT DO NOTHING")

if __name__ == '__main__':
    initialize()

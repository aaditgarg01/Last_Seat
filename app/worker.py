"""Durable polling worker. Multiple replicas are safe via seat row locks."""
import logging
import signal
import time
from app.db import engine, rows
from app.service import lock_seat, refresh

log = logging.getLogger(__name__)
stopping = False

def tick():
    with engine.connect() as c:
        candidates = rows(c,"""SELECT DISTINCT r.seat_id FROM reservations r
          LEFT JOIN payments p ON p.reservation_id=r.id
          WHERE r.status='held' AND (r.expires_at<=clock_timestamp() OR
          (p.status='pending' AND p.due_at<=clock_timestamp())) ORDER BY r.seat_id LIMIT 500""")
    for r in candidates:
        with engine.begin() as c:
            lock_seat(c,r['seat_id'])
            refresh(c,r['seat_id'])
    return len(candidates)

def stop(*_):
    global stopping
    stopping = True

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    signal.signal(signal.SIGTERM,stop)
    signal.signal(signal.SIGINT,stop)
    while not stopping:
        try:
            tick()
        except Exception:
            log.exception('Worker iteration failed; durable jobs will be retried')
        time.sleep(0.5)

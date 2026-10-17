"""Every reservation transition locks its seat first, then reads fresh state."""
import hashlib
import json
import os
from datetime import timedelta
from uuid import uuid4
from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from app.db import one, rows, run, now

TTL = int(os.getenv('RESERVATION_TTL_SECONDS', '120'))
if TTL < 1:
    raise ValueError('RESERVATION_TTL_SECONDS must be positive')


def reject(code, detail):
    raise HTTPException(code, detail)


def lock_seat(c, seat_id):
    if not one(c, 'SELECT id FROM seats WHERE id=:id FOR UPDATE', id=seat_id):
        reject(404, 'Seat not found')


def refresh(c, seat_id):
    """Caller holds seat lock. DB wall time is read AFTER lock acquisition."""
    r = one(c, "SELECT * FROM reservations WHERE seat_id=:s AND status IN ('held','confirmed')", s=seat_id)
    if not r or r['status'] != 'held':
        return r
    t = now(c)
    p = one(c, 'SELECT * FROM payments WHERE reservation_id=:r', r=r['id'])
    # Expiry takes precedence, even if a worker was down when payment became due.
    if r['expires_at'] <= t:
        run(c, "UPDATE reservations SET status='expired' WHERE id=:r", r=r['id'])
        run(c, "UPDATE payments SET status='voided' WHERE reservation_id=:r AND status='pending'", r=r['id'])
        return None
    if p and p['status'] == 'pending' and p['due_at'] <= t:
        if p['outcome'] == 'success':
            run(c, "UPDATE payments SET status='succeeded' WHERE id=:p", p=p['id'])
            run(c, "UPDATE reservations SET status='confirmed', confirmed_at=:t WHERE id=:r", t=t, r=r['id'])
        else:
            run(c, "UPDATE payments SET status='failed' WHERE id=:p", p=p['id'])
            run(c, "UPDATE reservations SET status='cancelled',cancelled_at=:t WHERE id=:r", t=t,r=r['id'])
            return None
    return one(c, 'SELECT * FROM reservations WHERE id=:r', r=r['id'])


def owned(c, rid, uid):
    r = one(c, 'SELECT * FROM reservations WHERE id=:r AND user_id=:u', r=rid,u=uid)
    if not r:
        reject(404, 'Reservation not found')
    lock_seat(c, r['seat_id'])
    refresh(c, r['seat_id'])
    return one(c, 'SELECT * FROM reservations WHERE id=:r', r=rid)


def view(c, rid):
    r = dict(one(c, 'SELECT * FROM reservations WHERE id=:r', r=rid))
    p = one(c, 'SELECT id,status,due_at FROM payments WHERE reservation_id=:r', r=rid)
    r['payment'] = dict(p) if p else None
    return jsonable_encoder(r)


def reserve(c, uid, seat_id):
    lock_seat(c, seat_id)
    if one(c, 'SELECT opens_at > clock_timestamp() AS closed FROM events WHERE id=1')['closed']:
        reject(403, 'Booking has not opened')
    if refresh(c, seat_id):
        reject(409, 'Seat is unavailable')
    rid = uuid4()
    run(c, "INSERT INTO reservations(id,user_id,seat_id,status,expires_at) VALUES (:r,:u,:s,'held',:e)",
        r=rid,u=uid,s=seat_id,e=now(c)+timedelta(seconds=TTL))
    return view(c, rid), 201


def confirm(c, uid, rid, outcome, delay):
    r = owned(c,rid,uid)
    if r['status'] not in ('held','confirmed'):
        reject(409, 'Reservation is no longer active')
    if not one(c, 'SELECT id FROM payments WHERE reservation_id=:r',r=rid):
        run(c,"INSERT INTO payments(id,reservation_id,status,outcome,due_at) VALUES (:p,:r,'pending',:o,:d)",
            p=uuid4(),r=rid,o=outcome,d=now(c)+timedelta(seconds=delay))
    refresh(c,r['seat_id'])
    result = view(c,rid)
    return result, 202 if result['payment']['status']=='pending' else 200


def cancel(c, uid, rid):
    r = owned(c,rid,uid)
    if r['status'] in ('held','confirmed'):
        run(c,"UPDATE reservations SET status='cancelled',cancelled_at=clock_timestamp() WHERE id=:r", r=rid)
        run(c,"UPDATE payments SET status=CASE WHEN status='succeeded' THEN 'refunded' ELSE 'voided' END WHERE reservation_id=:r AND status IN ('pending','succeeded')",r=rid)
    return view(c,rid),200


def idempotent(c, uid, key, operation, payload, action):
    fingerprint = hashlib.sha256(json.dumps([operation,payload],sort_keys=True).encode()).hexdigest()
    # Same user+key waits for the original transaction, including across API replicas.
    run(c,'SELECT pg_advisory_xact_lock(hashtextextended(:k,0))',k=f'{uid}:{key}')
    old = one(c,'SELECT * FROM idempotency WHERE user_id=:u AND key=:k',u=uid,k=key)
    if old:
        if old['fingerprint'] != fingerprint:
            reject(409,'Idempotency key reused for a different request')
        return old['response'],old['code']
    # Save deterministic application failures too; release any locks at savepoint rollback.
    try:
        with c.begin_nested():
            result,code = action()
    except HTTPException as exc:
        result,code = {'detail':exc.detail},exc.status_code
    result = jsonable_encoder(result)
    run(c,'INSERT INTO idempotency(user_id,key,fingerprint,response,code) VALUES (:u,:k,:f,CAST(:r AS jsonb),:c)',
        u=uid,k=key,f=fingerprint,r=json.dumps(result),c=code)
    return result,code

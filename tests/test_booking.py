"""Integration tests: use a DEDICATED PostgreSQL database (tables are truncated)."""
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.init_db import initialize
from app.db import engine, run, one
from app.worker import tick

client = TestClient(app)

@pytest.fixture(autouse=True)
def clean():
    if os.getenv('ALLOW_TEST_RESET') != '1':
        pytest.fail('Set ALLOW_TEST_RESET=1 with a dedicated test database')
    initialize()
    with engine.begin() as c:
        run(c,'TRUNCATE idempotency,payments,reservations,users CASCADE')
        run(c,"UPDATE events SET opens_at='2026-01-01T00:00:00Z'")

def headers():
    token = client.post('/users',json={'name':'Student'}).json()['token']
    return {'Authorization':f'Bearer {token}'}

def post(path, h, data=None, key=None):
    return client.post(path,headers={**h,'Idempotency-Key':key or str(uuid4())},json=data)

def hold(h, seat=1):
    response = post('/reservations',h,{'seat_id':seat})
    assert response.status_code==201,response.text
    return response.json()['id']

def age(rid):
    with engine.begin() as c:
        run(c,"UPDATE reservations SET expires_at=clock_timestamp()-interval '1 second' WHERE id=CAST(:r AS uuid)",r=rid)

def test_same_seat_concurrently():
    hs = [headers() for _ in range(80)]
    with ThreadPoolExecutor(max_workers=40) as pool:
        results = list(pool.map(lambda h:post('/reservations',h,{'seat_id':1}),hs))
    assert sum(r.status_code==201 for r in results)==1
    assert sum(r.status_code==409 for r in results)==79
    with engine.connect() as c:
        assert one(c,"SELECT count(*) n FROM reservations WHERE status='held'")['n']==1

def test_concurrent_duplicate_requests_and_conflicting_key():
    h = headers()
    with ThreadPoolExecutor(max_workers=20) as pool:
        rs = list(pool.map(lambda _:post('/reservations',h,{'seat_id':2},'same'),range(30)))
    assert all(r.status_code==201 for r in rs)
    assert len({r.json()['id'] for r in rs})==1
    assert post('/reservations',h,{'seat_id':3},'same').status_code==409

def test_expiry_and_reallocation_without_worker():
    a,b = headers(),headers()
    rid = hold(a)
    age(rid)
    assert hold(b)!=rid
    assert client.get(f'/reservations/{rid}',headers=a).json()['status']=='expired'

def test_worker_expires_abandoned_hold():
    h = headers()
    rid = hold(h)
    age(rid)
    assert tick()==1
    assert client.get('/seats').json()[0]['status']=='available'

def test_success_and_refund():
    h = headers()
    rid = hold(h)
    r = post(f'/reservations/{rid}/confirm',h,{'outcome':'success','delay_seconds':0})
    assert r.status_code==200
    assert r.json()['status']=='confirmed'
    assert r.json()['payment']['status']=='succeeded'
    r = post(f'/reservations/{rid}/cancel',h)
    assert r.json()['payment']['status']=='refunded'
    hold(headers())

def test_failed_payment_releases_seat():
    h = headers()
    rid = hold(h)
    r = post(f'/reservations/{rid}/confirm',h,{'outcome':'failure'})
    assert r.json()['status']=='cancelled'
    assert r.json()['payment']['status']=='failed'
    hold(headers())

def test_delayed_payment_cannot_resurrect_expired_reservation():
    h = headers()
    rid = hold(h)
    assert post(f'/reservations/{rid}/confirm',h,{'delay_seconds':60}).status_code==202
    age(rid)
    newer = hold(headers())
    with engine.begin() as c:
        run(c,"UPDATE payments SET due_at=clock_timestamp()-interval '1 second'")
    tick()
    r = client.get(f'/reservations/{rid}',headers=h).json()
    assert r['status']=='expired'
    assert r['payment']['status']=='voided'
    assert newer!=rid

def test_pending_payment_survives_worker_restart():
    h = headers()
    rid = hold(h)
    post(f'/reservations/{rid}/confirm',h,{'delay_seconds':20})
    with engine.begin() as c:
        run(c,"UPDATE payments SET due_at=clock_timestamp()-interval '1 second'")
    tick()
    assert client.get(f'/reservations/{rid}',headers=h).json()['status']=='confirmed'

def test_cancel_pending_payment():
    h = headers()
    rid = hold(h)
    post(f'/reservations/{rid}/confirm',h,{'delay_seconds':30})
    assert post(f'/reservations/{rid}/cancel',h).json()['payment']['status']=='voided'
    hold(headers())

def test_confirm_cancel_race():
    h = headers()
    rid = hold(h)
    with ThreadPoolExecutor(max_workers=2) as p:
        a = p.submit(post,f'/reservations/{rid}/confirm',h,{'outcome':'success'})
        b = p.submit(post,f'/reservations/{rid}/cancel',h)
        assert a.result().status_code in (200,409)
        assert b.result().status_code==200
    r = client.get(f'/reservations/{rid}',headers=h).json()
    assert r['status']=='cancelled'
    assert r['payment'] is None or r['payment']['status'] in ('voided','refunded')

def test_payment_duplicates_different_keys_only_charge_once():
    h = headers()
    rid = hold(h)
    with ThreadPoolExecutor(max_workers=20) as p:
        rs = list(p.map(lambda _:post(f'/reservations/{rid}/confirm',h,{}),range(25)))
    assert all(r.status_code==200 for r in rs)
    with engine.connect() as c:
        assert one(c,'SELECT count(*) n FROM payments')['n']==1

def test_ownership_history_and_validation():
    h,other = headers(),headers()
    rid = hold(h)
    assert client.get(f'/reservations/{rid}',headers=other).status_code==404
    assert post(f'/reservations/{rid}/cancel',other).status_code==404
    assert len(client.get('/bookings',headers=h).json())==1
    assert client.get('/bookings',headers=other).json()==[]
    assert client.post('/reservations',json={'seat_id':1}).status_code==401
    assert post('/reservations',h,{'seat_id':501}).status_code==422
    assert client.post('/reservations',headers=h,json={'seat_id':2}).status_code==422

def test_opening_time_enforced():
    h = headers()
    with engine.begin() as c:
        run(c,"UPDATE events SET opens_at=clock_timestamp()+interval '1 day'")
    assert post('/reservations',h,{'seat_id':1}).status_code==403

def test_database_unique_guard():
    from sqlalchemy.exc import IntegrityError
    h = headers()
    rid = hold(h)
    with pytest.raises(IntegrityError), engine.begin() as c:
        run(c,"""INSERT INTO reservations(id,user_id,seat_id,status,expires_at)
          SELECT :id,user_id,seat_id,'confirmed',expires_at FROM reservations WHERE id=CAST(:r AS uuid)""",id=uuid4(),r=rid)

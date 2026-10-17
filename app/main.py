import hashlib
import secrets
from typing import Annotated, Literal
from uuid import UUID, uuid4
from fastapi import FastAPI, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy.exc import OperationalError, TimeoutError
from app.db import engine, one, rows, run
from app import service

app = FastAPI(title='The Last Seat', version='1.0.0', description='500 seats. Transactional reservations with simulated payments. Run the worker for automatic expiry.')
bearer = HTTPBearer()
Key = Annotated[str, Header(alias='Idempotency-Key', min_length=1, max_length=128)]

class UserInput(BaseModel):
    name: str = Field(min_length=1,max_length=100)
class ReserveInput(BaseModel):
    seat_id: int = Field(ge=1,le=500)
class ConfirmInput(BaseModel):
    outcome: Literal['success','failure'] = 'success'
    delay_seconds: int = Field(default=0,ge=0,le=600)

def user(auth: Annotated[HTTPAuthorizationCredentials, Depends(bearer)]):
    with engine.connect() as c:
        u = one(c,'SELECT id FROM users WHERE token_hash=:h',h=hashlib.sha256(auth.credentials.encode()).hexdigest())
    if not u:
        raise HTTPException(401,'Invalid token')
    return u['id']
User = Annotated[UUID, Depends(user)]

@app.exception_handler(OperationalError)
@app.exception_handler(TimeoutError)
async def unavailable(request, exc):
    return JSONResponse({'detail':'Database temporarily unavailable; retry with the same idempotency key'},503,headers={'Retry-After':'1'})

@app.get('/health')
def health():
    with engine.connect() as c:
        one(c,'SELECT 1')
    return {'status':'ok'}

@app.post('/users',status_code=201)
def register(body: UserInput):
    token = secrets.token_urlsafe(32)
    uid = uuid4()
    with engine.begin() as c:
        run(c,'INSERT INTO users VALUES (:u,:n,:h)',u=uid,n=body.name,h=hashlib.sha256(token.encode()).hexdigest())
    return {'id':str(uid),'token':token}

@app.get('/events')
def events():
    with engine.connect() as c:
        return [dict(r) for r in rows(c,'SELECT * FROM events ORDER BY id')]

@app.get('/seats')
def seats():
    with engine.connect() as c:
        return [dict(r) for r in rows(c,"""SELECT s.id,s.label,CASE WHEN r.status='confirmed' THEN 'booked'
          WHEN r.status='held' AND r.expires_at>clock_timestamp() THEN 'reserved' ELSE 'available' END AS status
          FROM seats s LEFT JOIN reservations r ON r.seat_id=s.id AND r.status IN ('held','confirmed') ORDER BY s.id""")]

@app.post('/reservations')
def reserve(body: ReserveInput, uid: User, key: Key):
    with engine.begin() as c:
        data,code = service.idempotent(c,uid,key,'reserve',body.model_dump(),lambda: service.reserve(c,uid,body.seat_id))
    return JSONResponse(data,code)

@app.post('/reservations/{rid}/confirm')
def confirm(rid: UUID, body: ConfirmInput, uid: User, key: Key):
    with engine.begin() as c:
        data,code = service.idempotent(c,uid,key,f'confirm:{rid}',body.model_dump(),lambda: service.confirm(c,uid,rid,body.outcome,body.delay_seconds))
    return JSONResponse(data,code)

@app.post('/reservations/{rid}/cancel')
def cancel(rid: UUID, uid: User, key: Key):
    with engine.begin() as c:
        data,code = service.idempotent(c,uid,key,f'cancel:{rid}',{},lambda: service.cancel(c,uid,rid))
    return JSONResponse(data,code)

@app.get('/reservations/{rid}')
def status(rid: UUID, uid: User):
    with engine.begin() as c:
        service.owned(c,rid,uid)
        return service.view(c,rid)

@app.get('/bookings')
def history(uid: User, limit: int = Query(50,ge=1,le=200), offset: int = Query(0,ge=0)):
    with engine.begin() as c:
        # Bound work, and acquire multiple seat locks in global order to avoid deadlocks.
        result = rows(c,'SELECT id,seat_id FROM reservations WHERE user_id=:u ORDER BY created_at DESC,id LIMIT :l OFFSET :o',u=uid,l=limit,o=offset)
        for sid in sorted({r['seat_id'] for r in result}):
            service.lock_seat(c,sid)
            service.refresh(c,sid)
        return [service.view(c,r['id']) for r in result]

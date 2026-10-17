import os
from sqlalchemy import create_engine, text

engine = create_engine(os.getenv('DATABASE_URL', 'postgresql+psycopg://lastseat:lastseat@localhost:5432/lastseat'), pool_size=int(os.getenv('DB_POOL_SIZE', '10')), max_overflow=int(os.getenv('DB_MAX_OVERFLOW', '5')), pool_pre_ping=True)

def rows(conn, sql, **params):
    return conn.execute(text(sql), params).mappings().all()

def one(conn, sql, **params):
    return conn.execute(text(sql), params).mappings().first()

def run(conn, sql, **params):
    return conn.execute(text(sql), params)

def now(conn):
    return one(conn, 'SELECT clock_timestamp() AS t')['t']

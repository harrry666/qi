import psycopg2
import psycopg2.extras
import os
from dotenv import load_dotenv

load_dotenv()

_URL = os.environ.get('DATABASE_URL', '')
if _URL.startswith('postgres://'):
    _URL = _URL.replace('postgres://', 'postgresql://', 1)


class _DB:
    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=()):
        cur = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql, params)
        return cur

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()


def get_db():
    conn = psycopg2.connect(_URL)
    return _DB(conn)


def init_db():
    db = get_db()
    for stmt in [
        '''CREATE TABLE IF NOT EXISTS businesses (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            slug TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            address TEXT DEFAULT '',
            phone TEXT DEFAULT '',
            description TEXT DEFAULT '',
            created_at TIMESTAMPTZ DEFAULT NOW()
        )''',
        '''CREATE TABLE IF NOT EXISTS business_hours (
            id SERIAL PRIMARY KEY,
            business_id INTEGER NOT NULL,
            weekday INTEGER NOT NULL,
            open_time TEXT NOT NULL DEFAULT '09:00',
            close_time TEXT NOT NULL DEFAULT '18:00',
            is_closed INTEGER NOT NULL DEFAULT 0,
            UNIQUE(business_id, weekday)
        )''',
        '''CREATE TABLE IF NOT EXISTS services (
            id SERIAL PRIMARY KEY,
            business_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            name_sub TEXT DEFAULT '',
            duration_mins INTEGER NOT NULL DEFAULT 30,
            price REAL,
            is_active INTEGER NOT NULL DEFAULT 1,
            sort_order INTEGER NOT NULL DEFAULT 0
        )''',
        '''CREATE TABLE IF NOT EXISTS appointments (
            id SERIAL PRIMARY KEY,
            business_id INTEGER NOT NULL,
            service_id INTEGER NOT NULL,
            customer_name TEXT NOT NULL,
            phone TEXT NOT NULL,
            appointment_dt TEXT NOT NULL,
            comment TEXT DEFAULT '',
            status TEXT DEFAULT 'confirmed',
            created_at TIMESTAMPTZ DEFAULT NOW()
        )''',
        'ALTER TABLE businesses ADD COLUMN IF NOT EXISTS category TEXT DEFAULT \'\'',
        '''CREATE TABLE IF NOT EXISTS business_blackouts (
            id SERIAL PRIMARY KEY,
            business_id INTEGER NOT NULL,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            reason TEXT DEFAULT ''
        )''',
        '''CREATE TABLE IF NOT EXISTS password_reset_tokens (
            id SERIAL PRIMARY KEY,
            business_id INTEGER NOT NULL,
            token TEXT UNIQUE NOT NULL,
            expires_at TIMESTAMPTZ NOT NULL,
            used INTEGER NOT NULL DEFAULT 0
        )''',
        'ALTER TABLE services ADD COLUMN IF NOT EXISTS emoji TEXT DEFAULT \'\'',
        'ALTER TABLE services ADD COLUMN IF NOT EXISTS buffer_mins INTEGER NOT NULL DEFAULT 0',
        'ALTER TABLE appointments ADD COLUMN IF NOT EXISTS cancel_token TEXT',
        'ALTER TABLE appointments ADD COLUMN IF NOT EXISTS reminder_sent INTEGER NOT NULL DEFAULT 0',
    ]:
        db.execute(stmt)
    db.commit()
    db.close()

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


def upsert_customer(db, business_id, phone, name):
    import uuid
    row = db.execute(
        'SELECT id FROM customers WHERE business_id=%s AND phone=%s',
        (business_id, phone)
    ).fetchone()
    if row:
        if name:
            db.execute('UPDATE customers SET name=%s WHERE id=%s', (name, row['id']))
        return row['id']
    cur = db.execute(
        'INSERT INTO customers (business_id, phone, name, profile_token) VALUES (%s,%s,%s,%s) RETURNING id',
        (business_id, phone, name, str(uuid.uuid4()))
    )
    return cur.fetchone()['id']


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
        'ALTER TABLE businesses ADD COLUMN IF NOT EXISTS avatar_url TEXT DEFAULT \'\'',
        'ALTER TABLE businesses ADD COLUMN IF NOT EXISTS cover_url TEXT DEFAULT \'\'',
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
        'ALTER TABLE appointments ADD COLUMN IF NOT EXISTS merchant_note TEXT',
        'ALTER TABLE businesses ADD COLUMN IF NOT EXISTS banner_url TEXT',
        'ALTER TABLE services ADD COLUMN IF NOT EXISTS icon_url TEXT',
        'ALTER TABLE businesses ADD COLUMN IF NOT EXISTS api_token TEXT',
        '''CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            openid TEXT UNIQUE NOT NULL,
            client_token TEXT UNIQUE NOT NULL,
            nickname TEXT DEFAULT '',
            created_at TIMESTAMPTZ DEFAULT NOW()
        )''',
        'ALTER TABLE appointments ADD COLUMN IF NOT EXISTS openid TEXT',
        'ALTER TABLE appointments ADD COLUMN IF NOT EXISTS subscribe_authed INTEGER DEFAULT 0',
        'ALTER TABLE appointments ADD COLUMN IF NOT EXISTS wx_reminder_sent INTEGER DEFAULT 0',
        'ALTER TABLE businesses ADD COLUMN IF NOT EXISTS is_approved INTEGER DEFAULT 1',
        '''CREATE TABLE IF NOT EXISTS staff (
            id SERIAL PRIMARY KEY,
            business_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            emoji TEXT DEFAULT '',
            avatar_url TEXT DEFAULT '',
            bio TEXT DEFAULT '',
            is_active INTEGER NOT NULL DEFAULT 1,
            sort_order INTEGER NOT NULL DEFAULT 0
        )''',
        '''CREATE TABLE IF NOT EXISTS staff_hours (
            id SERIAL PRIMARY KEY,
            staff_id INTEGER NOT NULL,
            weekday INTEGER NOT NULL,
            open_time TEXT NOT NULL DEFAULT '09:00',
            close_time TEXT NOT NULL DEFAULT '18:00',
            is_closed INTEGER NOT NULL DEFAULT 0,
            UNIQUE(staff_id, weekday)
        )''',
        '''CREATE TABLE IF NOT EXISTS staff_services (
            id SERIAL PRIMARY KEY,
            staff_id INTEGER NOT NULL,
            service_id INTEGER NOT NULL,
            UNIQUE(staff_id, service_id)
        )''',
        'ALTER TABLE appointments ADD COLUMN IF NOT EXISTS staff_id INTEGER',
        '''CREATE TABLE IF NOT EXISTS time_blocks (
            id SERIAL PRIMARY KEY,
            business_id INTEGER NOT NULL,
            staff_id INTEGER,
            date TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            reason TEXT DEFAULT ''
        )''',
        'ALTER TABLE services ADD COLUMN IF NOT EXISTS color TEXT DEFAULT \'\'',
        '''CREATE TABLE IF NOT EXISTS customers (
            id SERIAL PRIMARY KEY,
            business_id INTEGER NOT NULL,
            phone TEXT NOT NULL,
            name TEXT DEFAULT '',
            avatar_url TEXT DEFAULT '',
            preferences TEXT DEFAULT '',
            private_note TEXT DEFAULT '',
            balance INTEGER NOT NULL DEFAULT 0,
            profile_token TEXT UNIQUE,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE(business_id, phone)
        )''',
        '''CREATE TABLE IF NOT EXISTS customer_photos (
            id SERIAL PRIMARY KEY,
            customer_id INTEGER NOT NULL,
            appointment_id INTEGER,
            photo_url TEXT NOT NULL,
            note TEXT DEFAULT '',
            uploaded_by TEXT NOT NULL DEFAULT 'merchant',
            created_at TIMESTAMPTZ DEFAULT NOW()
        )''',
        '''CREATE TABLE IF NOT EXISTS balance_transactions (
            id SERIAL PRIMARY KEY,
            customer_id INTEGER NOT NULL,
            delta INTEGER NOT NULL,
            reason TEXT DEFAULT '',
            created_at TIMESTAMPTZ DEFAULT NOW()
        )''',
        'ALTER TABLE appointments ADD COLUMN IF NOT EXISTS customer_id INTEGER',
        'ALTER TABLE businesses ADD COLUMN IF NOT EXISTS support_contact TEXT DEFAULT \'\'',
        '''CREATE TABLE IF NOT EXISTS platform_feedback (
            id SERIAL PRIMARY KEY,
            source TEXT NOT NULL DEFAULT 'customer',
            business_id INTEGER,
            name TEXT DEFAULT '',
            contact TEXT DEFAULT '',
            message TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'new',
            created_at TIMESTAMPTZ DEFAULT NOW()
        )''',
    ]:
        db.execute(stmt)
    db.commit()
    db.close()

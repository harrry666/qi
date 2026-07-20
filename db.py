import psycopg2
import psycopg2.extras
import os
import re
from dotenv import load_dotenv
from flask import g, has_app_context
from extensions import db_pool

load_dotenv()

_URL = os.environ.get('DATABASE_URL', '')
if _URL.startswith('postgres://'):
    _URL = _URL.replace('postgres://', 'postgresql://', 1)


class _DB:
    def __init__(self, conn, pooled=False):
        self._conn = conn
        self._pooled = pooled
        self._closed = False

    def execute(self, sql, params=()):
        cur = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql, params)
        return cur

    def commit(self):
        self._conn.commit()

    def close(self):
        # 幂等：同一条连接绝不能 putconn 两次，那会让池里出现重复句柄，比泄露更糟
        if self._closed:
            return
        self._closed = True
        if self._pooled:
            self._conn.rollback()
            db_pool.putconn(self._conn)
        else:
            self._conn.close()


def get_db():
    db = _DB(db_pool.getconn(), pooled=True) if db_pool else _DB(psycopg2.connect(_URL))
    # 请求上下文里登记，交给 app.py 的 teardown_appcontext 兜底回收；
    # 脚本 / APScheduler / threading.Thread 没有 app context，走原来的裸路径不变
    if has_app_context():
        conns = getattr(g, '_open_dbs', None)
        if conns is None:
            conns = []
            g._open_dbs = conns
        conns.append(db)
    return db


def close_open_dbs():
    """teardown 兜底：把本次请求里还没关的连接逐个还给池，单个失败不影响其他。"""
    for db in getattr(g, '_open_dbs', []) or []:
        try:
            db.close()
        except Exception:
            pass


def normalize_phone(raw):
    """归一化为纯10位美国号码；11位带1去掉国码。无法识别时返回去符号后的数字。"""
    digits = re.sub(r'\D', '', raw or '')
    if len(digits) == 11 and digits.startswith('1'):
        digits = digits[1:]
    return digits[-10:] if len(digits) >= 10 else digits


def is_valid_phone(raw):
    """归一化后恰好10位才算有效美国手机号，防止填错号导致短信静默发不出。"""
    return len(normalize_phone(raw)) == 10


def upsert_customer(db, business_id, phone, name):
    import uuid
    phone = normalize_phone(phone)
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


# init_db 的排他锁 key。gunicorn 起 4 个 worker，每个都会 import app 各跑一遍 init_db，
# 而 CREATE TABLE IF NOT EXISTS / ADD COLUMN IF NOT EXISTS 在 Postgres 里不是并发安全的：
# 两个 session 同时判断"不存在"都通过，后到的那个会报 duplicate_table 让 worker 起不来。
# 用事务级 advisory lock 让它们排队，锁随 commit/rollback 自动释放，不会漏锁。
_INIT_LOCK_KEY = 8274512309


def init_db():
    db = get_db()
    db.execute('SELECT pg_advisory_xact_lock(%s)', (_INIT_LOCK_KEY,))
    stmts = [
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
        # 几乎所有查询都是 WHERE business_id=%s AND appointment_dt ...，customer_id 也常被查
        'CREATE INDEX IF NOT EXISTS idx_appointments_biz_dt ON appointments (business_id, appointment_dt)',
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
        # 休息日临时营业：针对某一天临时开放一个时间窗，staff_id 为空=全店开放
        '''CREATE TABLE IF NOT EXISTS business_open_overrides (
            id SERIAL PRIMARY KEY,
            business_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            open_time TEXT NOT NULL,
            close_time TEXT NOT NULL,
            staff_id INTEGER,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )''',
        'CREATE INDEX IF NOT EXISTS idx_open_overrides_biz_date ON business_open_overrides (business_id, date)',
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
        'ALTER TABLE businesses ADD COLUMN IF NOT EXISTS trial_ends_at TIMESTAMPTZ',
        "ALTER TABLE businesses ADD COLUMN IF NOT EXISTS subscription_status TEXT DEFAULT 'none'",
        'ALTER TABLE businesses ADD COLUMN IF NOT EXISTS stripe_customer_id TEXT',
        'ALTER TABLE businesses ADD COLUMN IF NOT EXISTS stripe_subscription_id TEXT',
        'ALTER TABLE businesses ADD COLUMN IF NOT EXISTS snap_minutes INTEGER DEFAULT 15',
        "UPDATE businesses SET trial_ends_at = NOW() + INTERVAL '30 days', subscription_status = 'trialing' WHERE trial_ends_at IS NULL",
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
        'ALTER TABLE services ADD COLUMN IF NOT EXISTS duration_min_mins INTEGER',
        'ALTER TABLE users ADD COLUMN IF NOT EXISTS avatar_url TEXT DEFAULT \'\'',
        'ALTER TABLE users ADD COLUMN IF NOT EXISTS phone TEXT DEFAULT \'\'',
        'ALTER TABLE users ADD COLUMN IF NOT EXISTS preferences TEXT DEFAULT \'\'',
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
        'ALTER TABLE customers ADD COLUMN IF NOT EXISTS is_blocked INTEGER NOT NULL DEFAULT 0',
        'ALTER TABLE appointments ADD COLUMN IF NOT EXISTS customer_id INTEGER',
        # 必须在上面这条 ALTER 之后：新库建表时 appointments 还没有 customer_id 列
        'CREATE INDEX IF NOT EXISTS idx_appointments_customer ON appointments (customer_id)',
        "ALTER TABLE appointments ADD COLUMN IF NOT EXISTS lang TEXT DEFAULT 'zh'",
        'ALTER TABLE businesses ADD COLUMN IF NOT EXISTS support_contact TEXT DEFAULT \'\'',
        'ALTER TABLE businesses ADD COLUMN IF NOT EXISTS calendar_token TEXT',
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
        '''CREATE TABLE IF NOT EXISTS broadcast_requests (
            id SERIAL PRIMARY KEY,
            business_id INTEGER NOT NULL,
            message TEXT NOT NULL,
            phones TEXT NOT NULL DEFAULT '[]',
            recipient_count INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'pending',
            sent_count INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            reviewed_at TIMESTAMPTZ
        )''',
        '''CREATE TABLE IF NOT EXISTS sms_usage (
            id SERIAL PRIMARY KEY,
            business_id INTEGER NOT NULL,
            segments INTEGER NOT NULL DEFAULT 1,
            kind TEXT NOT NULL DEFAULT 'other',
            to_phone TEXT DEFAULT '',
            created_at TIMESTAMPTZ DEFAULT NOW()
        )''',
        'CREATE INDEX IF NOT EXISTS idx_sms_usage_biz_month ON sms_usage (business_id, created_at)',
        # 院校合作：毕业生开店数据看板
        '''CREATE TABLE IF NOT EXISTS schools (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            slug TEXT UNIQUE NOT NULL,
            token TEXT UNIQUE NOT NULL,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )''',
        'ALTER TABLE businesses ADD COLUMN IF NOT EXISTS school_id INTEGER',
        # 毕业生注册时勾选，未勾选的店不进学院看板的任何统计
        'ALTER TABLE businesses ADD COLUMN IF NOT EXISTS school_consent INTEGER DEFAULT 0',
        'CREATE INDEX IF NOT EXISTS idx_businesses_school ON businesses (school_id)',
        # Backfill: some old appointments (e.g. WeChat mini-program bookings before
        # create_booking() linked customers) have phone/name but no customer_id.
        '''INSERT INTO customers (business_id, phone, name, profile_token)
            SELECT DISTINCT ON (a.business_id, a.phone) a.business_id, a.phone, a.customer_name,
                md5(random()::text || clock_timestamp()::text)
            FROM appointments a
            WHERE a.customer_id IS NULL AND a.phone IS NOT NULL AND a.phone <> ''
            ORDER BY a.business_id, a.phone, a.appointment_dt DESC
            ON CONFLICT (business_id, phone) DO NOTHING''',
        '''UPDATE appointments a SET customer_id = c.id
            FROM customers c
            WHERE a.customer_id IS NULL AND a.phone IS NOT NULL AND a.phone <> ''
            AND c.business_id = a.business_id AND c.phone = a.phone''',
    ]
    try:
        for stmt in stmts:
            db.execute(stmt)
        db.commit()
    finally:
        # 中途报错也要把连接还给池，否则 4 个 worker 轮流试、池很快被占空
        db.close()

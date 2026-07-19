"""院校看板回归测试。跑法: pytest tests/test_school.py

护的是隐私边界：没勾数据共享的毕业生店，绝对不能出现在学院看板的任何数字里。
这条错了就是把商家没同意的数据泄给第三方，改 blueprints/school.py 前必跑。
"""
from datetime import datetime, timedelta
import pytest

pytestmark = pytest.mark.db

SLUG_PREFIX = 'regtest-school-'
SCHOOL_SLUG = 'regtest-school'


def _cleanup(db):
    rows = db.execute("SELECT id FROM businesses WHERE slug LIKE %s", (SLUG_PREFIX + '%',)).fetchall()
    for r in rows:
        db.execute('DELETE FROM appointments WHERE business_id=%s', (r['id'],))
        db.execute('DELETE FROM services WHERE business_id=%s', (r['id'],))
        db.execute('DELETE FROM businesses WHERE id=%s', (r['id'],))
    db.execute('DELETE FROM schools WHERE slug=%s', (SCHOOL_SLUG,))
    db.commit()


def _make_shop(db, school_id, suffix, consent, bookings):
    db.execute(
        "INSERT INTO businesses (name, slug, email, password_hash, category, school_id, school_consent) "
        "VALUES (%s,%s,%s,'x','Nails',%s,%s)",
        ('RegTest ' + suffix, SLUG_PREFIX + suffix, SLUG_PREFIX + suffix + '@t.com', school_id, consent)
    )
    db.commit()
    bid = db.execute('SELECT id FROM businesses WHERE slug=%s', (SLUG_PREFIX + suffix,)).fetchone()['id']
    db.execute("INSERT INTO services (business_id, name, duration_mins, is_active) VALUES (%s,'S',30,1)", (bid,))
    db.commit()
    sid = db.execute('SELECT id FROM services WHERE business_id=%s', (bid,)).fetchone()['id']
    for i in range(bookings):
        dt = (datetime.now() - timedelta(days=i % 20)).strftime('%Y-%m-%d %H:%M')
        db.execute(
            "INSERT INTO appointments (business_id, service_id, customer_name, phone, appointment_dt) "
            "VALUES (%s,%s,'C','6265550000',%s)", (bid, sid, dt)
        )
    db.commit()
    return bid


def test_consent_gate():
    from db import get_db
    from blueprints.school import _stats
    db = get_db()
    _cleanup(db)
    db.execute("INSERT INTO schools (name, slug, token) VALUES ('RegTest School',%s,'regtest-token')",
               (SCHOOL_SLUG,))
    db.commit()
    school_id = db.execute('SELECT id FROM schools WHERE slug=%s', (SCHOOL_SLUG,)).fetchone()['id']
    try:
        _make_shop(db, school_id, 'yes-a', 1, 10)
        _make_shop(db, school_id, 'yes-b', 1, 6)
        _make_shop(db, school_id, 'no', 0, 100)

        s = _stats(db, school_id)
        assert s['total'] == 2, '没勾同意的店不能计入开店数'
        # 没同意那家有 100 单，混进来的话店均会爆掉
        assert s['avg_monthly_bookings'] == 8.0, '没勾同意的店的预约不能计入'
        assert s['active'] == 2
        assert sum(c['n'] for c in s['categories']) == 2, '品类分布也要排除没同意的店'
    finally:
        _cleanup(db)
        db.close()


def test_empty_school_returns_zero():
    from db import get_db
    from blueprints.school import _stats
    db = get_db()
    _cleanup(db)
    db.execute("INSERT INTO schools (name, slug, token) VALUES ('RegTest School',%s,'regtest-token')",
               (SCHOOL_SLUG,))
    db.commit()
    school_id = db.execute('SELECT id FROM schools WHERE slug=%s', (SCHOOL_SLUG,)).fetchone()['id']
    try:
        assert _stats(db, school_id)['total'] == 0
    finally:
        _cleanup(db)
        db.close()

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


def _make_shop(db, school_id, suffix, consent, bookings, cancelled=0):
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
    for i in range(bookings + cancelled):
        dt = (datetime.now() - timedelta(days=i % 20)).strftime('%Y-%m-%d %H:%M')
        status = 'confirmed' if i < bookings else 'cancelled'
        db.execute(
            "INSERT INTO appointments (business_id, service_id, customer_name, phone, appointment_dt, status) "
            "VALUES (%s,%s,'C','6265550000',%s,%s)", (bid, sid, dt, status)
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
        # 3 家是 MIN_SHOPS 阈值，低于这个数不出数字，见 test_min_sample_threshold
        _make_shop(db, school_id, 'yes-a', 1, 10)
        _make_shop(db, school_id, 'yes-b', 1, 6)
        _make_shop(db, school_id, 'yes-c', 1, 8)
        _make_shop(db, school_id, 'no', 0, 100)

        s = _stats(db, school_id)
        assert s['total'] == 3, '没勾同意的店不能计入开店数'
        # 没同意那家有 100 单，混进来的话店均会爆掉
        assert s['avg_monthly_bookings'] == 8.0, '没勾同意的店的预约不能计入'
        assert s['active'] == 3
        assert sum(c['n'] for c in s['categories']) == 3, '品类分布也要排除没同意的店'
    finally:
        _cleanup(db)
        db.close()


def test_cancelled_bookings_excluded():
    """已取消的预约不能算进活跃度和店均接单量，否则给学院看的数字虚高。"""
    from db import get_db
    from blueprints.school import _stats
    db = get_db()
    _cleanup(db)
    db.execute("INSERT INTO schools (name, slug, token) VALUES ('RegTest School',%s,'regtest-token')",
               (SCHOOL_SLUG,))
    db.commit()
    school_id = db.execute('SELECT id FROM schools WHERE slug=%s', (SCHOOL_SLUG,)).fetchone()['id']
    try:
        _make_shop(db, school_id, 'a', 1, 4, cancelled=20)
        _make_shop(db, school_id, 'b', 1, 2, cancelled=20)
        # 这家近 30 天只有取消单，不算「仍在接单」
        _make_shop(db, school_id, 'dead', 1, 0, cancelled=30)

        s = _stats(db, school_id)
        assert s['total'] == 3
        assert s['avg_monthly_bookings'] == 2.0, '取消单不能计入店均接单量 (6 confirmed / 3 店)'
        assert s['active'] == 2, '只剩取消单的店不算仍在接单'
    finally:
        _cleanup(db)
        db.close()


@pytest.mark.parametrize('n_shops', [1, 2])
def test_min_sample_threshold(n_shops):
    """同意共享的店不足 3 家时不能出任何经营数字，否则等于点名单个毕业生。"""
    from db import get_db
    from blueprints.school import _stats, MIN_SHOPS
    db = get_db()
    _cleanup(db)
    db.execute("INSERT INTO schools (name, slug, token) VALUES ('RegTest School',%s,'regtest-token')",
               (SCHOOL_SLUG,))
    db.commit()
    school_id = db.execute('SELECT id FROM schools WHERE slug=%s', (SCHOOL_SLUG,)).fetchone()['id']
    try:
        for i in range(n_shops):
            _make_shop(db, school_id, 'thr-%d' % i, 1, 7)

        s = _stats(db, school_id)
        assert s['total'] == n_shops, '开通家数本身可以告诉学院'
        assert s['ready'] is False
        for k in ('avg_months', 'active', 'active_pct', 'avg_monthly_bookings', 'categories'):
            assert k not in s, f'不足 {MIN_SHOPS} 家时 {k} 不能出现'
    finally:
        _cleanup(db)
        db.close()


def test_threshold_page_hides_numbers():
    """渲染层面兜底：1 家时页面上不能出现具体经营数字。"""
    from db import get_db
    from app import app
    db = get_db()
    _cleanup(db)
    db.execute("INSERT INTO schools (name, slug, token) VALUES ('RegTest School',%s,'regtest-token')",
               (SCHOOL_SLUG,))
    db.commit()
    school_id = db.execute('SELECT id FROM schools WHERE slug=%s', (SCHOOL_SLUG,)).fetchone()['id']
    try:
        _make_shop(db, school_id, 'solo', 1, 7)
        db.close()
        with app.test_client() as c:
            html = c.get('/school/regtest-token').get_data(as_text=True)
        assert '数据积累中' in html
        assert 'stat muted' in html, '四个数字位应该是占位的 — 而不是真实值'
        assert '存活率' not in html, '存活率是单店数据，不能渲染'
        assert '平均已经开了' not in html
    finally:
        db = get_db()
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

"""院校合作：毕业生开店数据看板。

学院拿一个只读 token 链接看聚合数据，不需要账号。
只统计「走学院链接注册 + 勾了共享同意」的店，没勾的只留归属不进任何数字。
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from flask import Blueprint, render_template, abort
from extensions import limiter
from db import get_db

school_bp = Blueprint('school', __name__)
_LA = ZoneInfo('America/Los_Angeles')

# 同意共享的店少于这个数就不出任何聚合数字。
# 学院知道谁是走自己链接注册的，1-2 家时「店均/平均」等于点名某个毕业生的真实经营数据，
# 跟页面 footer 承诺的「只含聚合数字」冲突。
MIN_SHOPS = 3

def _stats(db, school_id):
    # appointment_dt 是 'YYYY-MM-DD HH:MM' 文本，不能直接跟 NOW() 比，
    # 按 send_reminders() 的惯例算好字符串再传进去
    cutoff = (datetime.now(_LA) - timedelta(days=30)).strftime('%Y-%m-%d %H:%M')
    where = 'school_id=%s AND school_consent=1'
    total = db.execute(
        f'SELECT COUNT(*) AS n FROM businesses WHERE {where}', (school_id,)
    ).fetchone()['n']
    if total < MIN_SHOPS:
        return {'total': total, 'ready': False, 'min_shops': MIN_SHOPS}

    # 开店月数：注册到现在。不足 1 个月按 1 个月算，避免刚开的店把均值拉到 0
    months = db.execute(
        f"SELECT COALESCE(AVG(GREATEST(1, EXTRACT(EPOCH FROM (NOW() - created_at)) / 2592000)), 0) AS m "
        f'FROM businesses WHERE {where}', (school_id,)
    ).fetchone()['m']

    # 仍在营业：近 30 天有过预约
    active = db.execute(
        'SELECT COUNT(DISTINCT b.id) AS n FROM businesses b '
        'JOIN appointments a ON a.business_id = b.id '
        "WHERE b.school_id=%s AND b.school_consent=1 AND a.status='confirmed' AND a.appointment_dt >= %s",
        (school_id, cutoff)
    ).fetchone()['n']

    # 月均接单量：近 30 天总预约数 / 开店毕业生数
    recent = db.execute(
        'SELECT COUNT(*) AS n FROM appointments a JOIN businesses b ON a.business_id = b.id '
        "WHERE b.school_id=%s AND b.school_consent=1 AND a.status='confirmed' AND a.appointment_dt >= %s",
        (school_id, cutoff)
    ).fetchone()['n']

    cats = db.execute(
        f'SELECT COALESCE(NULLIF(category, %s), %s) AS category, COUNT(*) AS n '
        f'FROM businesses WHERE {where} GROUP BY 1 ORDER BY n DESC, 1',
        ('', 'Other', school_id)
    ).fetchall()

    return {
        'total': total,
        'ready': True,
        'min_shops': MIN_SHOPS,
        'avg_months': round(float(months), 1),
        'active': active,
        'active_pct': round(active / total * 100) if total else 0,
        'avg_monthly_bookings': round(recent / total, 1) if total else 0,
        'categories': [dict(c) for c in cats],
        'cat_max': max([c['n'] for c in cats]) if cats else 0,
    }

@school_bp.route('/school/<token>')
@limiter.limit('30 per minute')
def dashboard(token):
    db = get_db()
    school = db.execute('SELECT * FROM schools WHERE token=%s', (token,)).fetchone()
    if not school:
        db.close()
        abort(404)
    stats = _stats(db, school['id'])
    db.close()
    return render_template('school/dashboard.html', school=school, s=stats)

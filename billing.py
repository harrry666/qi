import math
from datetime import datetime, timezone

SOLO_PRICE = 15.00          # 一人店固定价
SEAT_PRICE = 10.00          # 两人及以上，每员工/月
PRICE_CAP = 39.99           # 团队版封顶，4 人及以上都是这个价
TRIAL_DAYS = 180            # 前 6 个月免费
SMS_SOLO = 200              # 一人店内含短信段数/月
SMS_TEAM = 600              # 团队版内含短信段数/月
SMS_OVERAGE_RATE = 0.02     # 超出后每段单价（成本约 $0.01225）

def seat_count(business_id):
    """计费席位数 = 在职员工数，至少 1（没建员工的一人店也算 1 席）。"""
    from db import get_db
    db = get_db()
    row = db.execute(
        'SELECT COUNT(*) AS n FROM staff WHERE business_id=%s AND is_active=1',
        (business_id,)
    ).fetchone()
    db.close()
    return max(1, int(row['n'] if row else 0))

def plan_for(seats):
    """按席位数算月费和短信配额。1 席 $15，2 席起 $10/席，封顶 $39.99。"""
    seats = max(1, int(seats))
    price = SOLO_PRICE if seats == 1 else min(seats * SEAT_PRICE, PRICE_CAP)
    return {
        'seats': seats,
        'price': round(price, 2),
        'sms_included': SMS_SOLO if seats == 1 else SMS_TEAM,
        'capped': seats > 1 and seats * SEAT_PRICE >= PRICE_CAP,
    }

def plan_of(business_id):
    return plan_for(seat_count(business_id))

def sms_usage(business_id, when=None):
    """本自然月短信用量。返回 used / included / over / overage_cost。"""
    from db import get_db
    now = when or datetime.now(timezone.utc)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    db = get_db()
    row = db.execute(
        'SELECT COALESCE(SUM(segments), 0) AS n FROM sms_usage '
        'WHERE business_id=%s AND created_at >= %s',
        (business_id, start)
    ).fetchone()
    db.close()
    used = int(row['n'] if row else 0)
    included = plan_of(business_id)['sms_included']
    over = max(0, used - included)
    return {
        'used': used,
        'included': included,
        'over': over,
        'overage_cost': round(over * SMS_OVERAGE_RATE, 2),
        'pct': min(100, round(used / included * 100)) if included else 0,
        'period_start': start,
    }

def _days_left(ends):
    if not ends:
        return 0
    now = datetime.now(timezone.utc)
    if ends.tzinfo is None:
        ends = ends.replace(tzinfo=timezone.utc)
    secs = (ends - now).total_seconds()
    return max(0, math.ceil(secs / 86400)) if secs > 0 else 0

def trial_days_left(business):
    return _days_left(getattr(business, 'trial_ends_at', None))

def has_access(status, trial_ends_at):
    """给公开页/接口用：按 status + 到期日判断商家是否还有权限。past_due/canceled/none 无权限。"""
    status = status or 'none'
    if status == 'active':
        return True
    if status in ('trialing', 'comp'):
        return _days_left(trial_ends_at) > 0
    return False

def sub_state(business):
    status = getattr(business, 'subscription_status', 'none') or 'none'
    days = trial_days_left(business)
    in_trial = status == 'trialing' and days > 0
    active = status == 'active'
    comp = status == 'comp' and days > 0
    # 只认还有效的订阅：canceled 之后 stripe_subscription_id 不会被清空，
    # 光看 id 存不存在会让账单页永远不渲染订阅按钮，商家被硬锁在账单页且无法自助复购。
    subscribed = (bool(getattr(business, 'stripe_subscription_id', None))
                  and status in ('trialing', 'active', 'past_due'))
    return {
        'status': status,
        'days_left': days,
        'in_trial': in_trial,
        'active': active,
        'comp': comp,
        'subscribed': subscribed,
        'trial_expired': status == 'trialing' and days <= 0,
        'has_access': active or in_trial or comp,
    }

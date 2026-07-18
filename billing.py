import math
from datetime import datetime, timezone

PLAN_PRICE = '29.99'
TRIAL_DAYS = 30
SMS_INCLUDED = 300          # 订阅内含短信段数/月
SMS_OVERAGE_RATE = 0.02     # 超出后每段单价（成本约 $0.01225）

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
    over = max(0, used - SMS_INCLUDED)
    return {
        'used': used,
        'included': SMS_INCLUDED,
        'over': over,
        'overage_cost': round(over * SMS_OVERAGE_RATE, 2),
        'pct': min(100, round(used / SMS_INCLUDED * 100)) if SMS_INCLUDED else 0,
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
    subscribed = bool(getattr(business, 'stripe_subscription_id', None))
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

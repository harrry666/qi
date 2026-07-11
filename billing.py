import math
from datetime import datetime, timezone

PLAN_PRICE = '19.99'
TRIAL_DAYS = 30

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

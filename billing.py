import math
from datetime import datetime, timezone

PLAN_PRICE = '19.99'
TRIAL_DAYS = 30

def trial_days_left(business):
    ends = getattr(business, 'trial_ends_at', None)
    if not ends:
        return 0
    now = datetime.now(timezone.utc)
    if ends.tzinfo is None:
        ends = ends.replace(tzinfo=timezone.utc)
    secs = (ends - now).total_seconds()
    return max(0, math.ceil(secs / 86400)) if secs > 0 else 0

def sub_state(business):
    status = getattr(business, 'subscription_status', 'none') or 'none'
    days = trial_days_left(business)
    in_trial = status == 'trialing' and days > 0
    active = status == 'active'
    subscribed = bool(getattr(business, 'stripe_subscription_id', None))
    return {
        'status': status,
        'days_left': days,
        'in_trial': in_trial,
        'active': active,
        'subscribed': subscribed,
        'trial_expired': status == 'trialing' and days <= 0,
        'has_access': active or in_trial,
    }

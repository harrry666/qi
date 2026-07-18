import os
import sys
import stripe
from flask import Blueprint, request, redirect, url_for, jsonify, flash
from flask_login import login_required, current_user
from db import get_db

stripe_bp = Blueprint('stripe_billing', __name__)

def _init():
    stripe.api_key = os.environ.get('STRIPE_SECRET_KEY', '')

def _base():
    return os.environ.get('BASE_URL', request.host_url).rstrip('/')

def _g(obj, key, default=None):
    # stripe 库对象不支持 .get()，用 bracket 访问兜底（同时兼容普通 dict）
    try:
        return obj[key]
    except (KeyError, TypeError):
        return default

def _map_status(sub_status):
    if sub_status in ('active', 'trialing', 'past_due', 'canceled'):
        return sub_status
    if sub_status in ('unpaid', 'incomplete_expired'):
        return 'canceled'
    return sub_status or 'none'

def _sync_subscription(business_id, sub):
    """把一个 Stripe 订阅对象同步进 businesses 表。"""
    status = _map_status(_g(sub, 'status'))
    end_ts = _g(sub, 'trial_end') or _g(sub, 'current_period_end')
    db = get_db()
    if end_ts:
        db.execute(
            "UPDATE businesses SET subscription_status=%s, stripe_subscription_id=%s, "
            "trial_ends_at=to_timestamp(%s) WHERE id=%s",
            (status, _g(sub, 'id'), end_ts, business_id)
        )
    else:
        db.execute(
            "UPDATE businesses SET subscription_status=%s, stripe_subscription_id=%s WHERE id=%s",
            (status, _g(sub, 'id'), business_id)
        )
    db.commit()
    db.close()

@stripe_bp.route('/dashboard/billing/checkout', methods=['POST'])
@login_required
def checkout():
    _init()
    if not stripe.api_key or not os.environ.get('STRIPE_PRICE_ID'):
        flash('flash.billing.not_configured', 'error')
        return redirect(url_for('dashboard.billing'))

    db = get_db()
    biz = db.execute('SELECT * FROM businesses WHERE id=%s', (current_user.id,)).fetchone()
    customer_id = biz.get('stripe_customer_id')
    if not customer_id:
        cust = stripe.Customer.create(email=biz['email'], name=biz['name'],
                                      metadata={'business_id': str(biz['id'])})
        customer_id = cust.id
        db.execute('UPDATE businesses SET stripe_customer_id=%s WHERE id=%s', (customer_id, biz['id']))
        db.commit()
    db.close()

    sub_data = {'metadata': {'business_id': str(current_user.id)}}
    from billing import sub_state
    days = sub_state(current_user)['days_left']
    if days >= 1:
        sub_data['trial_period_days'] = days

    line_items = [{'price': os.environ['STRIPE_PRICE_ID'], 'quantity': 1}]
    metered = os.environ.get('STRIPE_SMS_PRICE_ID', '')
    if metered:
        line_items.append({'price': metered})  # 用量计费项不能带 quantity

    session = stripe.checkout.Session.create(
        mode='subscription',
        customer=customer_id,
        line_items=line_items,
        subscription_data=sub_data,
        client_reference_id=str(current_user.id),
        success_url=f"{_base()}/dashboard/billing/success?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{_base()}/dashboard/billing",
        allow_promotion_codes=True,
    )
    return redirect(session.url, code=303)

def report_sms_overage(business_id, segments):
    """把超出配额的段数推给 Stripe Meter，按 $0.02/段累进下月账单。"""
    meter = os.environ.get('STRIPE_METER_EVENT_NAME', '')
    _init()
    if not (stripe.api_key and meter and segments > 0):
        return
    db = get_db()
    row = db.execute('SELECT stripe_customer_id, subscription_status FROM businesses WHERE id=%s',
                     (business_id,)).fetchone()
    db.close()
    # comp / 试用期商家不计超额费
    if not row or not row['stripe_customer_id'] or row['subscription_status'] != 'active':
        return
    try:
        stripe.billing.MeterEvent.create(
            event_name=meter,
            payload={'stripe_customer_id': row['stripe_customer_id'], 'value': str(segments)},
        )
    except Exception as e:
        print(f'[SMS] meter report failed biz={business_id} seg={segments}: {e}',
              flush=True, file=sys.stderr)

@stripe_bp.route('/dashboard/billing/success')
@login_required
def success():
    _init()
    sid = request.args.get('session_id', '')
    if sid and stripe.api_key:
        try:
            session = stripe.checkout.Session.retrieve(sid, expand=['subscription'])
            sub = _g(session, 'subscription')
            if sub and str(_g(session, 'client_reference_id')) == str(current_user.id):
                _sync_subscription(current_user.id, sub)
        except Exception:
            pass
    flash('flash.billing.subscribe_success', 'success')
    return redirect(url_for('dashboard.billing'))

@stripe_bp.route('/stripe/webhook', methods=['POST'])
def webhook():
    _init()
    payload = request.get_data()
    sig = request.headers.get('Stripe-Signature', '')
    secret = os.environ.get('STRIPE_WEBHOOK_SECRET', '')
    try:
        if secret:
            event = stripe.Webhook.construct_event(payload, sig, secret)
        else:
            event = stripe.Event.construct_from(request.get_json(force=True), stripe.api_key)
    except Exception:
        return jsonify({'error': 'invalid'}), 400

    typ = _g(event, 'type')
    obj = event['data']['object']

    def _biz_from_customer(cust_id):
        db = get_db()
        row = db.execute('SELECT id FROM businesses WHERE stripe_customer_id=%s', (cust_id,)).fetchone()
        db.close()
        return row['id'] if row else None

    if typ == 'checkout.session.completed':
        md = _g(obj, 'metadata') or {}
        bid = _g(md, 'business_id') or _g(obj, 'client_reference_id')
        sub_id = _g(obj, 'subscription')
        if bid and sub_id:
            sub = stripe.Subscription.retrieve(sub_id)
            _sync_subscription(int(bid), sub)
    elif typ in ('customer.subscription.updated', 'customer.subscription.created', 'customer.subscription.deleted'):
        md = _g(obj, 'metadata') or {}
        bid = _g(md, 'business_id') or _biz_from_customer(_g(obj, 'customer'))
        if bid:
            _sync_subscription(int(bid), obj)
    elif typ == 'invoice.payment_failed':
        bid = _biz_from_customer(_g(obj, 'customer'))
        if bid:
            db = get_db()
            db.execute("UPDATE businesses SET subscription_status='past_due' WHERE id=%s", (bid,))
            db.commit()
            db.close()

    return jsonify({'received': True}), 200

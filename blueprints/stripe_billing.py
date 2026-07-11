import os
import time
import stripe
from flask import Blueprint, request, redirect, url_for, jsonify, flash
from flask_login import login_required, current_user
from db import get_db

stripe_bp = Blueprint('stripe_billing', __name__)

def _init():
    stripe.api_key = os.environ.get('STRIPE_SECRET_KEY', '')

def _base():
    return os.environ.get('BASE_URL', request.host_url).rstrip('/')

def _map_status(sub_status):
    # Stripe 订阅状态 → 我们库里的 subscription_status
    if sub_status in ('active', 'trialing', 'past_due', 'canceled'):
        return sub_status
    if sub_status in ('unpaid', 'incomplete_expired'):
        return 'canceled'
    return sub_status or 'none'

def _sync_subscription(business_id, sub):
    """把一个 Stripe 订阅对象同步进 businesses 表。"""
    status = _map_status(sub.get('status'))
    end_ts = sub.get('trial_end') or sub.get('current_period_end')
    db = get_db()
    if end_ts:
        db.execute(
            "UPDATE businesses SET subscription_status=%s, stripe_subscription_id=%s, "
            "trial_ends_at=to_timestamp(%s) WHERE id=%s",
            (status, sub.get('id'), end_ts, business_id)
        )
    else:
        db.execute(
            "UPDATE businesses SET subscription_status=%s, stripe_subscription_id=%s WHERE id=%s",
            (status, sub.get('id'), business_id)
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
    ends = getattr(current_user, 'trial_ends_at', None)
    if ends:
        ts = int(ends.timestamp())
        if ts > int(time.time()) + 2 * 24 * 3600:
            sub_data['trial_end'] = ts

    session = stripe.checkout.Session.create(
        mode='subscription',
        customer=customer_id,
        line_items=[{'price': os.environ['STRIPE_PRICE_ID'], 'quantity': 1}],
        subscription_data=sub_data,
        client_reference_id=str(current_user.id),
        success_url=f"{_base()}/dashboard/billing/success?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{_base()}/dashboard/billing",
        allow_promotion_codes=True,
    )
    return redirect(session.url, code=303)

@stripe_bp.route('/dashboard/billing/success')
@login_required
def success():
    _init()
    sid = request.args.get('session_id', '')
    if sid and stripe.api_key:
        try:
            session = stripe.checkout.Session.retrieve(sid, expand=['subscription'])
            sub = session.get('subscription')
            if sub and str(session.get('client_reference_id')) == str(current_user.id):
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

    typ = event['type']
    obj = event['data']['object']

    if typ == 'checkout.session.completed':
        bid = (obj.get('metadata') or {}).get('business_id') or obj.get('client_reference_id')
        sub_id = obj.get('subscription')
        if bid and sub_id:
            sub = stripe.Subscription.retrieve(sub_id)
            _sync_subscription(int(bid), sub)
    elif typ in ('customer.subscription.updated', 'customer.subscription.created', 'customer.subscription.deleted'):
        bid = (obj.get('metadata') or {}).get('business_id')
        if not bid:
            db = get_db()
            row = db.execute('SELECT id FROM businesses WHERE stripe_customer_id=%s', (obj.get('customer'),)).fetchone()
            db.close()
            bid = row['id'] if row else None
        if bid:
            _sync_subscription(int(bid), obj)
    elif typ == 'invoice.payment_failed':
        db = get_db()
        row = db.execute('SELECT id FROM businesses WHERE stripe_customer_id=%s', (obj.get('customer'),)).fetchone()
        if row:
            db.execute("UPDATE businesses SET subscription_status='past_due' WHERE id=%s", (row['id'],))
            db.commit()
        db.close()

    return jsonify({'received': True}), 200

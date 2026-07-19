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

    # 模板层（billing.html 的 {% elif sub.subscribed %}）是唯一门禁，绕过前端直接 POST
    # 或者双击提交都会给同一个 customer 建出第二条订阅，两条都计费。这里用和模板同一个
    # sub_state 判断挡住。canceled 的 subscribed 为假，复购路径不受影响。
    from billing import sub_state, seat_count
    if sub_state(current_user)['subscribed']:
        flash('flash.billing.already_subscribed', 'error')
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
    days = sub_state(current_user)['days_left']
    if days >= 1:
        sub_data['trial_period_days'] = days

    # 席位阶梯价：quantity 传席位数，封顶 $39.99 交给 Stripe 的 volume tier，不在代码里夹。
    # 没配 STRIPE_SEAT_PRICE_ID 就退回旧的固定价单席位，避免拿旧的 flat price 乘席位数超收。
    seat_price_id = os.environ.get('STRIPE_SEAT_PRICE_ID', '')
    if seat_price_id:
        line_items = [{'price': seat_price_id, 'quantity': seat_count(current_user.id)}]
    else:
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

def sync_seats(business_id):
    """员工增减后把席位数同步给 Stripe 订阅。失败只记日志，不挡商家操作。"""
    _init()
    price_id = os.environ.get('STRIPE_SEAT_PRICE_ID', '')
    if not (stripe.api_key and price_id):
        return
    db = get_db()
    row = db.execute('SELECT stripe_subscription_id, subscription_status FROM businesses WHERE id=%s',
                     (business_id,)).fetchone()
    db.close()
    if not row or not row['stripe_subscription_id']:
        return
    # 已取消/无订阅的商家没有席位可同步，早退避免白打一次 Stripe 请求还记一条 error 日志
    status = row['subscription_status'] or 'none'
    if status in ('canceled', 'none'):
        return
    from billing import seat_count
    seats = seat_count(business_id)
    try:
        sub = stripe.Subscription.retrieve(row['stripe_subscription_id'])
        for item in _g(sub, 'items')['data']:
            if _g(_g(item, 'price'), 'id') == price_id:
                if _g(item, 'quantity') != seats:
                    stripe.SubscriptionItem.modify(_g(item, 'id'), quantity=seats,
                                                   proration_behavior='none')
                return
        # 一个 item 都没匹配上：商家可能走的是旧的 STRIPE_PRICE_ID 固定价，
        # 或者 Stripe 改了 price id 而环境变量没同步。不报错就永远查不出来席位为什么不动。
        print(f'[BILLING] seat sync skipped biz={business_id} seats={seats}: '
              f'sub={row["stripe_subscription_id"]} 没有 price={price_id} 的 item，'
              f'现有 items={[_g(_g(i, "price"), "id") for i in _g(sub, "items")["data"]]}',
              flush=True, file=sys.stderr)
    except Exception as e:
        print(f'[BILLING] seat sync failed biz={business_id} seats={seats}: {e}',
              flush=True, file=sys.stderr)

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

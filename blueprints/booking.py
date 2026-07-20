from flask import Blueprint, render_template, request, jsonify, redirect, url_for, Response, g
from db import get_db, normalize_phone
from extensions import limiter
from translations import t
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

_LA = ZoneInfo('America/Los_Angeles')
import os
import re
import math
import threading
import sys
import uuid

booking_bp = Blueprint('booking', __name__)

SLOT_INTERVAL = 30

TWILIO_SID        = os.environ.get('TWILIO_SID', '')
TWILIO_TOKEN      = os.environ.get('TWILIO_TOKEN', '')
TWILIO_FROM       = os.environ.get('TWILIO_FROM', '')
TWILIO_VERIFY_SID = os.environ.get('TWILIO_VERIFY_SID', '')


def format_phone(raw):
    digits = re.sub(r'\D', '', raw)
    if len(digits) == 10:
        return f'+1{digits}'
    if len(digits) == 11 and digits.startswith('1'):
        return f'+{digits}'
    return f'+1{digits}'


def count_segments(message):
    """短信段数。含非 GSM 字符（如中文）走 UCS-2：单段 70 字，多段 67 字。"""
    unicode_msg = any(ord(c) > 127 for c in message)
    limit, multi = (70, 67) if unicode_msg else (160, 153)
    n = len(message)
    return 1 if n <= limit else math.ceil(n / multi)


def record_sms(business_id, segments, kind='other', to_phone=''):
    if not business_id:
        return
    try:
        db = get_db()
        db.execute(
            'INSERT INTO sms_usage (business_id, segments, kind, to_phone) VALUES (%s, %s, %s, %s)',
            (business_id, segments, kind, to_phone)
        )
        db.commit()
        db.close()
    except Exception as e:
        print(f'[SMS] usage record failed biz={business_id}: {e}', flush=True, file=sys.stderr)
        return
    try:
        from billing import sms_usage
        from blueprints.stripe_billing import report_sms_overage
        usage = sms_usage(business_id)
        # 只上报这批里真正跨过配额线的那部分，避免重复计费
        billable = min(segments, max(0, usage['used'] - usage['included']))
        if billable > 0:
            report_sms_overage(business_id, billable)
    except Exception as e:
        print(f'[SMS] overage check failed biz={business_id}: {e}', flush=True, file=sys.stderr)


def send_sms(to_phone, message, business_id=None, kind='other'):
    if not all([TWILIO_SID, TWILIO_TOKEN, TWILIO_FROM]):
        print(f'[SMS] credentials missing, skip {to_phone}', flush=True, file=sys.stderr)
        return
    try:
        from twilio.rest import Client
        msg = Client(TWILIO_SID, TWILIO_TOKEN).messages.create(
            body=message, from_=TWILIO_FROM, to=to_phone
        )
        segments = int(getattr(msg, 'num_segments', 0) or 0) or count_segments(message)
        record_sms(business_id, segments, kind, to_phone)
        print(f'[SMS] sent to {to_phone} ({segments} seg, biz={business_id})', flush=True, file=sys.stderr)
    except Exception as e:
        print(f'[SMS] FAILED {to_phone}: {e}', flush=True, file=sys.stderr)


def get_biz_by_slug(slug):
    db = get_db()
    biz = db.execute('SELECT * FROM businesses WHERE slug=%s', (slug,)).fetchone()
    db.close()
    return biz

def generate_slots(business_id, date_obj, duration_mins, staff_id=None):
    db = get_db()
    biz_bh = db.execute(
        'SELECT * FROM business_hours WHERE business_id=%s AND weekday=%s',
        (business_id, date_obj.weekday())
    ).fetchone()
    staff_bh = None
    if staff_id:
        staff_bh = db.execute(
            'SELECT * FROM staff_hours WHERE staff_id=%s AND weekday=%s',
            (staff_id, date_obj.weekday())
        ).fetchone()
    ds = date_obj.strftime('%Y-%m-%d')
    blacked = db.execute(
        'SELECT id FROM business_blackouts WHERE business_id=%s AND start_date<=%s AND end_date>=%s',
        (business_id, ds, ds)
    ).fetchone()
    db.close()

    # 店铺当天休业/黑名单 = 硬关闭，员工个人排班也盖不过去，想约只能走加班预约
    if not biz_bh or biz_bh['is_closed'] or blacked:
        return []
    bh = staff_bh or biz_bh
    if bh['is_closed']:
        return []

    sh, sm = map(int, bh['open_time'].split(':'))
    eh, em = map(int, bh['close_time'].split(':'))
    current = datetime(date_obj.year, date_obj.month, date_obj.day, sh, sm)
    end = datetime(date_obj.year, date_obj.month, date_obj.day, eh, em)

    now_la = datetime.now(_LA).replace(tzinfo=None)
    is_today = (date_obj == now_la.date())
    slots = []
    while current + timedelta(minutes=duration_mins) <= end:
        if not is_today or current > now_la:
            slots.append(current.strftime('%H:%M'))
        current += timedelta(minutes=SLOT_INTERVAL)
    return slots

def filter_available(business_id, date_str, slots, duration_mins, staff_id=None):
    db = get_db()
    if staff_id:
        booked = db.execute(
            "SELECT s.duration_mins, s.buffer_mins, a.appointment_dt FROM appointments a "
            "JOIN services s ON a.service_id=s.id "
            "WHERE a.business_id=%s AND a.staff_id=%s AND a.appointment_dt LIKE %s AND a.status != 'cancelled'",
            (business_id, staff_id, f'{date_str}%')
        ).fetchall()
    else:
        booked = db.execute(
            "SELECT s.duration_mins, s.buffer_mins, a.appointment_dt FROM appointments a "
            "JOIN services s ON a.service_id=s.id "
            "WHERE a.business_id=%s AND a.appointment_dt LIKE %s AND a.status != 'cancelled'",
            (business_id, f'{date_str}%')
        ).fetchall()
    if staff_id:
        blocks = db.execute(
            "SELECT start_time, end_time FROM time_blocks WHERE business_id=%s AND date=%s AND (staff_id=%s OR staff_id IS NULL)",
            (business_id, date_str, staff_id)
        ).fetchall()
    else:
        blocks = db.execute(
            "SELECT start_time, end_time FROM time_blocks WHERE business_id=%s AND date=%s",
            (business_id, date_str)
        ).fetchall()
    db.close()

    occupied = []
    for b in booked:
        b_dt = datetime.strptime(b['appointment_dt'], '%Y-%m-%d %H:%M')
        occupied.append((b_dt, b_dt + timedelta(minutes=b['duration_mins'] + b['buffer_mins'])))
    for bl in blocks:
        occupied.append((
            datetime.strptime(f'{date_str} {bl["start_time"]}', '%Y-%m-%d %H:%M'),
            datetime.strptime(f'{date_str} {bl["end_time"]}', '%Y-%m-%d %H:%M')
        ))

    available = []
    for slot in slots:
        slot_dt = datetime.strptime(f'{date_str} {slot}', '%Y-%m-%d %H:%M')
        slot_end = slot_dt + timedelta(minutes=duration_mins)
        conflict = any(not (slot_end <= o_start or slot_dt >= o_end) for o_start, o_end in occupied)
        if not conflict:
            available.append(slot)
    return available

def get_active_staff_for_service(business_id, service_id):
    db = get_db()
    rows = db.execute(
        "SELECT st.* FROM staff st JOIN staff_services ss ON st.id=ss.staff_id "
        "WHERE st.business_id=%s AND st.is_active=1 AND ss.service_id=%s "
        "ORDER BY st.sort_order, st.id",
        (business_id, service_id)
    ).fetchall()
    db.close()
    return [dict(r) for r in rows]

def slots_for_service(business_id, date_obj, duration_mins, service_id, staff_id=None):
    ds = date_obj.strftime('%Y-%m-%d')
    if staff_id:
        sl = generate_slots(business_id, date_obj, duration_mins, staff_id=int(staff_id))
        return filter_available(business_id, ds, sl, duration_mins, staff_id=int(staff_id))
    candidates = get_active_staff_for_service(business_id, service_id)
    if not candidates:
        sl = generate_slots(business_id, date_obj, duration_mins)
        return filter_available(business_id, ds, sl, duration_mins)
    union = set()
    for st in candidates:
        sl = generate_slots(business_id, date_obj, duration_mins, staff_id=st['id'])
        union |= set(filter_available(business_id, ds, sl, duration_mins, staff_id=st['id']))
    return sorted(union)

def resolve_staff_id(business_id, service_id, date_str, time_str, duration_mins, staff_id=None):
    if staff_id:
        return int(staff_id)
    candidates = get_active_staff_for_service(business_id, service_id)
    if not candidates:
        return None
    for st in candidates:
        if filter_available(business_id, date_str, [time_str], duration_mins, staff_id=st['id']):
            return st['id']
    return candidates[0]['id']

@booking_bp.route('/book/<slug>')
def book_page(slug):
    biz = get_biz_by_slug(slug)
    if not biz:
        return '<h2 style="font-family:sans-serif;padding:40px">Business not found.</h2>', 404
    from billing import has_access
    if not has_access(biz.get('subscription_status'), biz.get('trial_ends_at')):
        return render_template('book_paused.html', biz=biz), 403
    return render_template('book.html', biz=biz, verify_enabled=bool(TWILIO_VERIFY_SID))

@booking_bp.route('/api/book/<slug>/services')
def api_services(slug):
    biz = get_biz_by_slug(slug)
    if not biz:
        return jsonify({'error': 'Not found'}), 404
    db = get_db()
    svcs = db.execute(
        'SELECT * FROM services WHERE business_id=%s AND is_active=1 ORDER BY sort_order, id',
        (biz['id'],)
    ).fetchall()
    db.close()
    return jsonify([dict(s) for s in svcs])

@booking_bp.route('/api/book/<slug>/staff')
def api_staff(slug):
    biz = get_biz_by_slug(slug)
    if not biz:
        return jsonify({'error': 'Not found'}), 404
    try:
        service_id = int(request.args.get('service_id', 0))
    except (ValueError, TypeError):
        return jsonify([])
    staff = get_active_staff_for_service(biz['id'], service_id)
    return jsonify([
        {'id': s['id'], 'name': s['name'], 'emoji': s['emoji'], 'avatar_url': s['avatar_url']}
        for s in staff
    ])

@booking_bp.route('/api/book/<slug>/week_slots')
def api_week_slots(slug):
    biz = get_biz_by_slug(slug)
    if not biz:
        return jsonify({'error': 'Not found'}), 404

    start_str = request.args.get('start')
    service_id = int(request.args.get('service_id', 0))
    staff_id = request.args.get('staff_id') or None

    db = get_db()
    svc = db.execute('SELECT * FROM services WHERE id=%s AND business_id=%s', (service_id, biz['id'])).fetchone()
    db.close()
    if not svc:
        return jsonify({'error': 'Service not found'}), 404

    duration = svc['duration_mins']
    start = datetime.strptime(start_str, '%Y-%m-%d').date()
    result = {}
    for i in range(7):
        d = start + timedelta(days=i)
        ds = d.strftime('%Y-%m-%d')
        result[ds] = slots_for_service(biz['id'], d, duration, service_id, staff_id=staff_id)

    return jsonify(result)

@booking_bp.route('/api/book/<slug>/create', methods=['POST'])
@limiter.limit('5 per minute; 30 per hour')
def api_create(slug):
    biz = get_biz_by_slug(slug)
    if not biz:
        return jsonify({'error': 'Not found'}), 404
    from billing import has_access
    if not has_access(biz.get('subscription_status'), biz.get('trial_ends_at')):
        return jsonify({'error': t('flash.booking.paused')}), 403

    data = request.json or {}
    if data.get('hp'):
        return jsonify({'success': True, 'service': 'OK'})
    service_id = data.get('service_id')
    name = (data.get('customer_name') or '').strip()
    from db import normalize_phone
    phone = normalize_phone((data.get('phone') or '').strip())
    apt_dt = data.get('appointment_dt')
    comment = (data.get('comment') or '').strip()

    if not all([service_id, name, phone, apt_dt]):
        return jsonify({'error': 'Missing required fields'}), 400
    if len(phone) != 10:
        return jsonify({'error': t('flash.common.phone_invalid')}), 400

    _bdb = get_db()
    _blocked = _bdb.execute(
        'SELECT 1 FROM customers WHERE business_id=%s AND phone=%s AND is_blocked=1',
        (biz['id'], phone)
    ).fetchone()
    _bdb.close()
    if _blocked:
        # 不明说被拉黑，引导致电门店，避免客人炸毛
        return jsonify({'error': t('flash.booking.blocked')}), 403

    try:
        apt_dt_obj = datetime.strptime(apt_dt, '%Y-%m-%d %H:%M')
        if apt_dt_obj < datetime.now(_LA).replace(tzinfo=None):
            return jsonify({'error': '不能预约过去的时间'}), 400
        apt_dt = apt_dt_obj.strftime('%Y-%m-%d %H:%M')
    except ValueError:
        return jsonify({'error': 'Invalid appointment time'}), 400

    if TWILIO_VERIFY_SID:
        verify_code = (data.get('verify_code') or '').strip()
        if not verify_code:
            return jsonify({'error': '请输入手机验证码'}), 400
        try:
            from twilio.rest import Client as _TwilioClient
            _check = _TwilioClient(TWILIO_SID, TWILIO_TOKEN).verify.v2.services(TWILIO_VERIFY_SID).verification_checks.create(
                to=format_phone(phone), code=verify_code
            )
            if _check.status != 'approved':
                return jsonify({'error': '验证码错误或已过期'}), 400
        except Exception:
            return jsonify({'error': '验证失败，请重新获取验证码'}), 400

    cancel_token = str(uuid.uuid4())

    db = get_db()
    svc = db.execute('SELECT * FROM services WHERE id=%s AND business_id=%s', (service_id, biz['id'])).fetchone()
    if not svc:
        db.close()
        return jsonify({'error': 'Service not found'}), 404
    db.close()

    a_date, a_time = apt_dt.split(' ')
    date_obj = datetime.strptime(a_date, '%Y-%m-%d').date()
    if a_time not in slots_for_service(biz['id'], date_obj, svc['duration_mins'], service_id, staff_id=data.get('staff_id') or None):
        return jsonify({'error': '该时段已被预约或不可用，请重新选择时间'}), 409
    staff_id = resolve_staff_id(biz['id'], service_id, a_date, a_time, svc['duration_mins'], data.get('staff_id') or None)

    db = get_db()
    from db import upsert_customer
    customer_id = upsert_customer(db, biz['id'], phone, name)
    lang = getattr(g, 'lang', 'zh')
    db.execute(
        'INSERT INTO appointments (business_id, service_id, customer_name, phone, appointment_dt, comment, cancel_token, staff_id, customer_id, lang) '
        'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
        (biz['id'], service_id, name, phone, apt_dt, comment, cancel_token, staff_id, customer_id, lang)
    )
    db.commit()
    db.close()

    try:
        dt = datetime.strptime(apt_dt, '%Y-%m-%d %H:%M')
        dt_display = dt.strftime('%Y年%-m月%-d日 %-H:%M')
        dt_display_en = dt.strftime('%b %-d, %Y %-H:%M')
    except Exception:
        dt_display = apt_dt
        dt_display_en = apt_dt

    formatted_phone = format_phone(phone)
    biz_phone = biz['phone'] or ''

    if lang == 'en':
        customer_msg = (
            f"[Confirmed] {name}, your {biz['name']} appointment is set.\n"
            f"Service: {svc['name']}\n"
            f"Time: {dt_display_en}\n"
            + (f"Addr: {biz['address']}\n" if biz['address'] else '')
            + (f"Call {biz_phone}. " if biz_phone else '')
            + "Reply CANCEL to cancel."
        )
    else:
        customer_msg = (
            f"【预约确认】{name} 您在 {biz['name']} 的预约已确认\n"
            f"服务：{svc['name']}\n"
            f"时间：{dt_display}\n"
            + (f"地址：{biz['address']}\n" if biz['address'] else '')
            + (f"问询致电{biz_phone}，" if biz_phone else '')
            + "取消回复「取消」"
        )
    threading.Thread(target=send_sms, args=(formatted_phone, customer_msg, biz['id'], 'confirm'), daemon=True).start()

    if biz_phone:
        last4 = re.sub(r'[^0-9]', '', phone)[-4:]
        owner_msg = (
            f"【新预约】{name} {phone}\n"
            f"{svc['name']}｜{dt_display}\n"
            + (f"备注：{comment}\n" if comment else '')
            + f"取消回复「取消 {last4}」"
        )
        threading.Thread(target=send_sms, args=(format_phone(biz_phone), owner_msg, biz['id'], 'owner_new'), daemon=True).start()

    return jsonify({'success': True, 'service': svc['name']})


@booking_bp.route('/sms/incoming', methods=['POST'])
def sms_incoming():
    from twilio.twiml.messaging_response import MessagingResponse
    from twilio.request_validator import RequestValidator
    if TWILIO_TOKEN:
        validator = RequestValidator(TWILIO_TOKEN)
        signature = request.headers.get('X-Twilio-Signature', '')
        _base = os.environ.get('BASE_URL', '').rstrip('/')
        url = (_base + request.full_path.rstrip('?')) if _base else request.url
        if not validator.validate(url, request.form.to_dict(), signature):
            print(f'[SMS] rejected: bad Twilio signature from {request.remote_addr}', flush=True, file=sys.stderr)
            return Response('Forbidden', status=403)
    body = (request.form.get('Body') or '').strip()
    from_phone = request.form.get('From', '')

    resp = MessagingResponse()
    from_digits = re.sub(r'\D', '', from_phone)
    ten = from_digits[-10:] if len(from_digits) >= 10 else from_digits
    now_str = datetime.now(_LA).replace(tzinfo=None).strftime('%Y-%m-%d %H:%M')

    db = get_db()
    try:
        # 先判断发信人是否是商家
        merchant = db.execute(
            "SELECT * FROM businesses WHERE "
            "REGEXP_REPLACE(phone,'[^0-9]','','g')=%s "
            "OR REGEXP_REPLACE(phone,'[^0-9]','','g')=%s",
            (from_digits, ten)
        ).fetchone()

        cancel_keywords = ['取消', 'cancel', 'c', 'quit', '1']
        is_cancel = any(k in body.lower() for k in cancel_keywords)
        merchant_cmd = re.search(r'(?:取消|cancel)\s*(\d{4})', body, re.IGNORECASE) if merchant else None

        if merchant_cmd:
            # 商家明确指定了客人后4位 → 走商家取消流程
            merchant = dict(merchant)
            last4 = merchant_cmd.group(1)
            apt = db.execute(
                "SELECT a.*, s.name as service_name "
                "FROM appointments a "
                "JOIN services s ON a.service_id=s.id "
                "WHERE a.business_id=%s AND a.status='confirmed' AND a.appointment_dt >= %s "
                "AND RIGHT(REGEXP_REPLACE(a.phone,'[^0-9]','','g'),4)=%s "
                "ORDER BY a.appointment_dt ASC LIMIT 1",
                (merchant['id'], now_str, last4)
            ).fetchone()
            if apt:
                apt = dict(apt)
                db.execute(
                    "UPDATE appointments SET status='cancelled' WHERE id=%s AND status='confirmed'",
                    (apt['id'],)
                )
                db.commit()
                try:
                    dt = datetime.strptime(apt['appointment_dt'], '%Y-%m-%d %H:%M')
                    dt_display = dt.strftime('%Y年%-m月%-d日 %-H:%M')
                    dt_display_en = dt.strftime('%b %-d, %Y %-H:%M')
                except Exception:
                    dt_display = apt['appointment_dt']
                    dt_display_en = apt['appointment_dt']
                if apt.get('lang') == 'en':
                    customer_msg = (
                        f"[Cancelled by Business] {apt['customer_name']}, your appointment at {merchant['name']} has been cancelled by the business.\n"
                        f"Service: {apt['service_name']}\n"
                        f"Was scheduled: {dt_display_en}\n"
                        + (f"Questions? Call {merchant['phone']}" if merchant.get('phone') else '')
                    )
                else:
                    customer_msg = (
                        f"【商家取消】{apt['customer_name']}，您在【{merchant['name']}】的预约已被商家取消。\n\n"
                        f"服务：{apt['service_name']}\n"
                        f"原定时间：{dt_display}\n"
                        + (f"如有疑问请致电：{merchant['phone']}" if merchant.get('phone') else '')
                    )
                threading.Thread(target=send_sms, args=(format_phone(apt['phone']), customer_msg, apt['business_id'], 'cancel_by_biz'), daemon=True).start()
                resp.message(f'已取消 {apt["customer_name"]}（尾号{last4}）的预约（{dt_display}）。')
            else:
                resp.message(f'未找到手机尾号为 {last4} 的待取消预约。')
        elif is_cancel:
            # 纯"取消"：先查发信人是否有客人预约（商家手机也可能是客人手机）
            apt = db.execute(
                "SELECT a.*, s.name as service_name, b.name as biz_name, b.phone as biz_phone "
                "FROM appointments a "
                "JOIN services s ON a.service_id=s.id "
                "JOIN businesses b ON a.business_id=b.id "
                "WHERE a.status='confirmed' AND a.appointment_dt >= %s "
                "AND (REGEXP_REPLACE(a.phone,'[^0-9]','','g')=%s "
                "     OR REGEXP_REPLACE(a.phone,'[^0-9]','','g')=%s) "
                "ORDER BY a.appointment_dt ASC LIMIT 1",
                (now_str, from_digits, ten)
            ).fetchone()
            if apt:
                apt = dict(apt)
                db.execute(
                    "UPDATE appointments SET status='cancelled' WHERE id=%s AND status='confirmed'",
                    (apt['id'],)
                )
                db.commit()
                try:
                    dt = datetime.strptime(apt['appointment_dt'], '%Y-%m-%d %H:%M')
                    dt_display = dt.strftime('%Y年%-m月%-d日 %-H:%M')
                    dt_display_en = dt.strftime('%b %-d, %Y %-H:%M')
                except Exception:
                    dt_display = apt['appointment_dt']
                    dt_display_en = apt['appointment_dt']
                if apt.get('biz_phone'):
                    owner_msg = (
                        f"【预约取消】{apt['biz_name']}\n\n"
                        f"客人：{apt['customer_name']}\n"
                        f"服务：{apt['service_name']}\n"
                        f"原定时间：{dt_display}"
                    )
                    threading.Thread(target=send_sms, args=(format_phone(apt['biz_phone']), owner_msg, apt['business_id'], 'owner_cancel'), daemon=True).start()
                resp.message(f'已取消您在【{apt["biz_name"]}】的预约（{dt_display}）。如需重新预约，请打开哈瓜小约。')
            elif merchant:
                # 发信人是商家但没有客人预约，提示商家格式
                resp.message('未找到您的待取消预约。如需取消客人预约，请发送「取消 客人手机后4位」。')
            else:
                resp.message('未找到待取消的预约。如有问题，请直接联系商家。')
        else:
            if merchant:
                resp.message('发送「取消 客人手机后4位」可取消该客人的预约。')
            else:
                resp.message('回复「取消」可取消您最近的预约。如需帮助，请直接联系商家。')
    finally:
        db.close()

    return str(resp), 200, {'Content-Type': 'text/xml'}


@booking_bp.route('/cancel/<token>', methods=['GET', 'POST'])
def cancel_by_token(token):
    db = get_db()
    row = db.execute(
        "SELECT a.*, s.name as service_name, b.name as biz_name, b.phone as biz_phone "
        "FROM appointments a "
        "JOIN services s ON a.service_id = s.id "
        "JOIN businesses b ON a.business_id = b.id "
        "WHERE a.cancel_token = %s",
        (token,)
    ).fetchone()

    if not row:
        db.close()
        return render_template('cancel.html', error=True)

    apt = dict(row)
    try:
        dt = datetime.strptime(apt['appointment_dt'], '%Y-%m-%d %H:%M')
        apt['dt_display'] = dt.strftime('%b %-d, %Y %-H:%M') if getattr(g, 'lang', 'zh') == 'en' else dt.strftime('%Y年%-m月%-d日 %-H:%M')
        apt['dt_display_zh'] = dt.strftime('%Y年%-m月%-d日 %-H:%M')
        apt['dt_display_en'] = dt.strftime('%b %-d, %Y %-H:%M')
    except Exception:
        apt['dt_display'] = apt['appointment_dt']
        apt['dt_display_zh'] = apt['appointment_dt']
        apt['dt_display_en'] = apt['appointment_dt']

    if request.method == 'POST':
        if apt['status'] != 'confirmed':
            db.close()
            return render_template('cancel.html', already_cancelled=True, apt=apt)

        db.execute(
            "UPDATE appointments SET status='cancelled' WHERE cancel_token=%s AND status='confirmed'",
            (token,)
        )
        db.commit()
        db.close()

        if apt['biz_phone']:
            msg = (
                f"【预约取消】{apt['biz_name']}\n\n"
                f"客人：{apt['customer_name']}\n"
                f"服务：{apt['service_name']}\n"
                f"原定时间：{apt['dt_display_zh']}"
            )
            threading.Thread(target=send_sms, args=(format_phone(apt['biz_phone']), msg, apt['business_id'], 'owner_cancel'), daemon=True).start()

        return render_template('cancel.html', success=True, apt=apt)

    if apt['status'] != 'confirmed':
        db.close()
        return render_template('cancel.html', already_cancelled=True, apt=apt)

    db.close()
    return render_template('cancel.html', apt=apt)


@booking_bp.route('/feedback', methods=['GET', 'POST'])
def public_feedback():
    slug = request.values.get('biz', '').strip()
    biz = get_biz_by_slug(slug) if slug else None

    sent = False
    if request.method == 'POST':
        if request.form.get('hp'):
            return render_template('feedback.html', biz=biz, sent=True)
        name = request.form.get('name', '').strip()
        contact = request.form.get('contact', '').strip()
        message = request.form.get('message', '').strip()
        if message:
            db = get_db()
            db.execute(
                "INSERT INTO platform_feedback (source, business_id, name, contact, message) VALUES ('customer',%s,%s,%s,%s)",
                (biz['id'] if biz else None, name, contact, message)
            )
            db.commit()
            db.close()
            sent = True

    return render_template('feedback.html', biz=biz, sent=sent)


@booking_bp.route('/book/<slug>/my', methods=['GET', 'POST'])
def my_request(slug):
    biz = get_biz_by_slug(slug)
    if not biz:
        return render_template('cancel.html', error=True)

    sent = False
    error = None
    if request.method == 'POST':
        phone = normalize_phone(request.form.get('phone', '').strip())
        db = get_db()
        cust = db.execute(
            'SELECT * FROM customers WHERE business_id=%s AND phone=%s',
            (biz['id'], phone)
        ).fetchone()
        db.close()
        if cust:
            base_url = os.environ.get('BASE_URL', request.host_url).rstrip('/')
            link = f"{base_url}/my/{cust['profile_token']}"
            msg = f"【{biz['name']}】你的专属客户档案链接：\n{link}\n\n可以设置偏好、上传照片。请勿转发给他人。"
            threading.Thread(target=send_sms, args=(format_phone(phone), msg, biz['id'], 'profile_link'), daemon=True).start()
            sent = True
        else:
            error = t('flash.myrequest.not_found')

    return render_template('my_request.html', biz=biz, sent=sent, error=error)


@booking_bp.route('/my/<token>')
def my_profile(token):
    db = get_db()
    cust = db.execute(
        'SELECT c.*, b.name as biz_name, b.slug as biz_slug FROM customers c '
        'JOIN businesses b ON c.business_id = b.id WHERE c.profile_token=%s',
        (token,)
    ).fetchone()
    if not cust:
        db.close()
        return render_template('cancel.html', error=True)
    visits = db.execute(
        "SELECT a.appointment_dt, a.status, s.name as service_name FROM appointments a "
        "JOIN services s ON a.service_id=s.id WHERE a.customer_id=%s ORDER BY a.appointment_dt DESC LIMIT 10",
        (cust['id'],)
    ).fetchall()
    photos = db.execute(
        "SELECT * FROM customer_photos WHERE customer_id=%s ORDER BY created_at DESC",
        (cust['id'],)
    ).fetchall()
    db.close()
    return render_template('my_profile.html', c=cust, visits=visits, photos=photos)


@booking_bp.route('/my/<token>/update', methods=['POST'])
def my_profile_update(token):
    from cloud import upload_to_cloudinary
    db = get_db()
    cust = db.execute('SELECT id FROM customers WHERE profile_token=%s', (token,)).fetchone()
    if cust:
        name = request.form.get('name', '').strip()
        preferences = request.form.get('preferences', '').strip()
        db.execute('UPDATE customers SET name=%s, preferences=%s WHERE id=%s', (name, preferences, cust['id']))
        avatar_file = request.files.get('avatar')
        if avatar_file and avatar_file.filename:
            avatar_url = upload_to_cloudinary(
                avatar_file, folder='qi/customers',
                transformation=[{'width': 300, 'height': 300, 'crop': 'fill'}]
            )
            if avatar_url:
                db.execute('UPDATE customers SET avatar_url=%s WHERE id=%s', (avatar_url, cust['id']))
        db.commit()
    db.close()
    return redirect(url_for('booking.my_profile', token=token, saved=1))


@booking_bp.route('/my/<token>/avatar', methods=['POST'])
def my_profile_avatar(token):
    from cloud import upload_to_cloudinary, destroy_urls
    db = get_db()
    cust = db.execute('SELECT id, avatar_url FROM customers WHERE profile_token=%s', (token,)).fetchone()
    saved = False
    if cust:
        avatar_url = upload_to_cloudinary(
            request.files.get('avatar'), folder='qi/customers',
            transformation=[{'width': 300, 'height': 300, 'crop': 'fill'}]
        )
        if avatar_url:
            db.execute('UPDATE customers SET avatar_url=%s WHERE id=%s', (avatar_url, cust['id']))
            db.commit()
            saved = True
    db.close()
    if saved:
        destroy_urls(cust['avatar_url'])
        return redirect(url_for('booking.my_profile', token=token, saved=1))
    return redirect(url_for('booking.my_profile', token=token, failed=1))


def _ical_escape(text):
    return (text or '').replace('\\', '\\\\').replace(';', '\\;').replace(',', '\\,').replace('\n', '\\n')


@booking_bp.route('/ical/<token>.ics')
def calendar_feed(token):
    db = get_db()
    biz = db.execute('SELECT * FROM businesses WHERE calendar_token=%s', (token,)).fetchone()
    if not biz:
        db.close()
        return Response('Not found', status=404)
    rows = db.execute(
        "SELECT a.id, a.customer_name, a.appointment_dt, a.comment, "
        "s.name as service_name, s.duration_mins, st.name as staff_name "
        "FROM appointments a JOIN services s ON a.service_id=s.id "
        "LEFT JOIN staff st ON a.staff_id=st.id "
        "WHERE a.business_id=%s AND a.status='confirmed' "
        "AND a.appointment_dt >= %s "
        "ORDER BY a.appointment_dt",
        (biz['id'], (datetime.now(_LA) - timedelta(days=7)).strftime('%Y-%m-%d %H:%M'))
    ).fetchall()
    db.close()

    lines = [
        'BEGIN:VCALENDAR',
        'VERSION:2.0',
        'PRODID:-//Hastrid Booking//' + biz['slug'] + '//EN',
        'CALSCALE:GREGORIAN',
        'X-WR-CALNAME:' + _ical_escape(biz['name'] + ' 预约日历'),
        'REFRESH-INTERVAL;VALUE=DURATION:PT15M',
    ]
    now_utc = datetime.now(ZoneInfo('UTC')).strftime('%Y%m%dT%H%M%SZ')
    for r in rows:
        try:
            start_local = datetime.strptime(r['appointment_dt'], '%Y-%m-%d %H:%M').replace(tzinfo=_LA)
        except ValueError:
            continue
        end_local = start_local + timedelta(minutes=r['duration_mins'] or 30)
        start_utc = start_local.astimezone(ZoneInfo('UTC')).strftime('%Y%m%dT%H%M%SZ')
        end_utc = end_local.astimezone(ZoneInfo('UTC')).strftime('%Y%m%dT%H%M%SZ')
        summary = f"{r['customer_name']} · {r['service_name']}"
        if r['staff_name']:
            summary += f"（{r['staff_name']}）"
        lines += [
            'BEGIN:VEVENT',
            'UID:apt-' + str(r['id']) + '@hastridbooking',
            'DTSTAMP:' + now_utc,
            'DTSTART:' + start_utc,
            'DTEND:' + end_utc,
            'SUMMARY:' + _ical_escape(summary),
            'DESCRIPTION:' + _ical_escape(r['comment'] or ''),
            'END:VEVENT',
        ]
    lines.append('END:VCALENDAR')
    body = '\r\n'.join(lines) + '\r\n'
    return Response(body, mimetype='text/calendar', headers={'Content-Disposition': 'inline; filename="hastrid.ics"'})


@booking_bp.route('/my/<token>/photo', methods=['POST'])
def my_profile_photo(token):
    from cloud import upload_to_cloudinary
    db = get_db()
    cust = db.execute('SELECT id FROM customers WHERE profile_token=%s', (token,)).fetchone()
    saved = False
    if cust:
        photo_url = upload_to_cloudinary(request.files.get('photo'), folder='qi/customer_photos', transformation=[{'width': 1200, 'height': 1200, 'crop': 'limit'}])
        note = request.form.get('note', '').strip()
        if photo_url:
            db.execute(
                "INSERT INTO customer_photos (customer_id, photo_url, note, uploaded_by) VALUES (%s,%s,%s,'customer')",
                (cust['id'], photo_url, note)
            )
            db.commit()
            saved = True
    db.close()
    if saved:
        return redirect(url_for('booking.my_profile', token=token, saved=1))
    return redirect(url_for('booking.my_profile', token=token, failed=1))

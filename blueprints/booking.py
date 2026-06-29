from flask import Blueprint, render_template, request, jsonify
from db import get_db
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

_LA = ZoneInfo('America/Los_Angeles')
import os
import re
import threading
import sys
import uuid

booking_bp = Blueprint('booking', __name__)

SLOT_INTERVAL = 30

TWILIO_SID   = os.environ.get('TWILIO_SID', '')
TWILIO_TOKEN = os.environ.get('TWILIO_TOKEN', '')
TWILIO_FROM  = os.environ.get('TWILIO_FROM', '')


def format_phone(raw):
    digits = re.sub(r'\D', '', raw)
    if len(digits) == 10:
        return f'+1{digits}'
    if len(digits) == 11 and digits.startswith('1'):
        return f'+{digits}'
    return f'+1{digits}'


def send_sms(to_phone, message):
    if not all([TWILIO_SID, TWILIO_TOKEN, TWILIO_FROM]):
        print(f'[SMS] credentials missing, skip {to_phone}', flush=True, file=sys.stderr)
        return
    try:
        from twilio.rest import Client
        Client(TWILIO_SID, TWILIO_TOKEN).messages.create(
            body=message, from_=TWILIO_FROM, to=to_phone
        )
        print(f'[SMS] sent to {to_phone}', flush=True, file=sys.stderr)
    except Exception as e:
        print(f'[SMS] FAILED {to_phone}: {e}', flush=True, file=sys.stderr)


def get_biz_by_slug(slug):
    db = get_db()
    biz = db.execute('SELECT * FROM businesses WHERE slug=%s', (slug,)).fetchone()
    db.close()
    return biz

def generate_slots(business_id, date_obj, duration_mins):
    db = get_db()
    bh = db.execute(
        'SELECT * FROM business_hours WHERE business_id=%s AND weekday=%s',
        (business_id, date_obj.weekday())
    ).fetchone()
    ds = date_obj.strftime('%Y-%m-%d')
    blacked = db.execute(
        'SELECT id FROM business_blackouts WHERE business_id=%s AND start_date<=%s AND end_date>=%s',
        (business_id, ds, ds)
    ).fetchone()
    db.close()

    if not bh or bh['is_closed'] or blacked:
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

def filter_available(business_id, date_str, slots, duration_mins):
    db = get_db()
    booked = db.execute(
        "SELECT s.duration_mins, s.buffer_mins, a.appointment_dt FROM appointments a "
        "JOIN services s ON a.service_id=s.id "
        "WHERE a.business_id=%s AND a.appointment_dt LIKE %s AND a.status != 'cancelled'",
        (business_id, f'{date_str}%')
    ).fetchall()
    db.close()

    available = []
    for slot in slots:
        slot_dt = datetime.strptime(f'{date_str} {slot}', '%Y-%m-%d %H:%M')
        slot_end = slot_dt + timedelta(minutes=duration_mins)
        conflict = False
        for b in booked:
            b_dt = datetime.strptime(b['appointment_dt'], '%Y-%m-%d %H:%M')
            b_end = b_dt + timedelta(minutes=b['duration_mins'] + b['buffer_mins'])
            if not (slot_end <= b_dt or slot_dt >= b_end):
                conflict = True
                break
        if not conflict:
            available.append(slot)
    return available

@booking_bp.route('/book/<slug>')
def book_page(slug):
    biz = get_biz_by_slug(slug)
    if not biz:
        return '<h2 style="font-family:sans-serif;padding:40px">Business not found.</h2>', 404
    return render_template('book.html', biz=biz)

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

@booking_bp.route('/api/book/<slug>/week_slots')
def api_week_slots(slug):
    biz = get_biz_by_slug(slug)
    if not biz:
        return jsonify({'error': 'Not found'}), 404

    start_str = request.args.get('start')
    service_id = int(request.args.get('service_id', 0))

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
        all_slots = generate_slots(biz['id'], d, duration)
        result[ds] = filter_available(biz['id'], ds, all_slots, duration)

    return jsonify(result)

@booking_bp.route('/api/book/<slug>/create', methods=['POST'])
def api_create(slug):
    biz = get_biz_by_slug(slug)
    if not biz:
        return jsonify({'error': 'Not found'}), 404

    data = request.json or {}
    if data.get('hp'):
        return jsonify({'success': True, 'service': 'OK'})
    service_id = data.get('service_id')
    name = (data.get('customer_name') or '').strip()
    phone = (data.get('phone') or '').strip()
    apt_dt = data.get('appointment_dt')
    comment = (data.get('comment') or '').strip()

    if not all([service_id, name, phone, apt_dt]):
        return jsonify({'error': 'Missing required fields'}), 400

    try:
        apt_dt_obj = datetime.strptime(apt_dt, '%Y-%m-%d %H:%M')
        if apt_dt_obj < datetime.now(_LA).replace(tzinfo=None):
            return jsonify({'error': '不能预约过去的时间'}), 400
        apt_dt = apt_dt_obj.strftime('%Y-%m-%d %H:%M')
    except ValueError:
        return jsonify({'error': 'Invalid appointment time'}), 400

    cancel_token = str(uuid.uuid4())

    db = get_db()
    svc = db.execute('SELECT * FROM services WHERE id=%s AND business_id=%s', (service_id, biz['id'])).fetchone()
    if not svc:
        db.close()
        return jsonify({'error': 'Service not found'}), 404

    db.execute(
        'INSERT INTO appointments (business_id, service_id, customer_name, phone, appointment_dt, comment, cancel_token) VALUES (%s,%s,%s,%s,%s,%s,%s)',
        (biz['id'], service_id, name, phone, apt_dt, comment, cancel_token)
    )
    db.commit()
    db.close()

    try:
        dt = datetime.strptime(apt_dt, '%Y-%m-%d %H:%M')
        dt_display = dt.strftime('%Y年%-m月%-d日 %-H:%M')
    except Exception:
        dt_display = apt_dt

    cancel_url = f"{request.host_url.rstrip('/')}cancel/{cancel_token}"
    formatted_phone = format_phone(phone)
    biz_phone = biz['phone'] or ''

    customer_msg = (
        f"【预约确认】{name}，您在【{biz['name']}】的预约已确认。\n\n"
        f"服务：{svc['name']}\n"
        f"时间：{dt_display}\n"
        + (f"地址：{biz['address']}\n" if biz['address'] else '')
        + (f"如有疑问请致电：{biz_phone}\n" if biz_phone else '')
        + f"\n如需取消：{cancel_url}"
    )
    threading.Thread(target=send_sms, args=(formatted_phone, customer_msg), daemon=True).start()

    if biz_phone:
        owner_msg = (
            f"【新预约】{biz['name']}\n\n"
            f"客人：{name}\n"
            f"电话：{phone}\n"
            f"服务：{svc['name']}\n"
            f"时间：{dt_display}\n"
            + (f"备注：{comment}" if comment else '')
        )
        threading.Thread(target=send_sms, args=(format_phone(biz_phone), owner_msg), daemon=True).start()

    return jsonify({'success': True, 'service': svc['name']})


@booking_bp.route('/sms/incoming', methods=['POST'])
def sms_incoming():
    from twilio.twiml.messaging_response import MessagingResponse
    body = (request.form.get('Body') or '').strip()
    from_phone = request.form.get('From', '')

    resp = MessagingResponse()
    cancel_keywords = ['取消', 'cancel', 'c', 'quit', '1']
    if any(k in body.lower() for k in cancel_keywords):
        from_digits = re.sub(r'\D', '', from_phone)
        ten = from_digits[-10:] if len(from_digits) >= 10 else from_digits
        now_str = datetime.now(_LA).replace(tzinfo=None).strftime('%Y-%m-%d %H:%M')
        db = get_db()
        try:
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
                except Exception:
                    dt_display = apt['appointment_dt']
                if apt.get('biz_phone'):
                    owner_msg = (
                        f"【预约取消】{apt['biz_name']}\n\n"
                        f"客人：{apt['customer_name']}\n"
                        f"服务：{apt['service_name']}\n"
                        f"原定时间：{dt_display}"
                    )
                    threading.Thread(target=send_sms, args=(format_phone(apt['biz_phone']), owner_msg), daemon=True).start()
                resp.message(f'已取消您在【{apt["biz_name"]}】的预约（{dt_display}）。如需重新预约，请打开哈瓜小约。')
            else:
                resp.message('未找到待取消的预约。如有问题，请直接联系商家。')
        finally:
            db.close()
    else:
        resp.message('回复「取消」可取消您最近的预约。如需帮助，请直接联系商家。')

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
        apt['dt_display'] = dt.strftime('%Y年%-m月%-d日 %-H:%M')
    except Exception:
        apt['dt_display'] = apt['appointment_dt']

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
                f"原定时间：{apt['dt_display']}"
            )
            threading.Thread(target=send_sms, args=(format_phone(apt['biz_phone']), msg), daemon=True).start()

        return render_template('cancel.html', success=True, apt=apt)

    if apt['status'] != 'confirmed':
        db.close()
        return render_template('cancel.html', already_cancelled=True, apt=apt)

    db.close()
    return render_template('cancel.html', apt=apt)

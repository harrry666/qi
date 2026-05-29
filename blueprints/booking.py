from flask import Blueprint, render_template, request, jsonify
from db import get_db
from datetime import datetime, timedelta

booking_bp = Blueprint('booking', __name__)

SLOT_INTERVAL = 30

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
    db.close()

    if not bh or bh['is_closed']:
        return []

    sh, sm = map(int, bh['open_time'].split(':'))
    eh, em = map(int, bh['close_time'].split(':'))
    current = datetime(date_obj.year, date_obj.month, date_obj.day, sh, sm)
    end = datetime(date_obj.year, date_obj.month, date_obj.day, eh, em)

    slots = []
    while current + timedelta(minutes=duration_mins) <= end:
        slots.append(current.strftime('%H:%M'))
        current += timedelta(minutes=SLOT_INTERVAL)
    return slots

def filter_available(business_id, date_str, slots, duration_mins):
    db = get_db()
    booked = db.execute(
        "SELECT s.duration_mins, a.appointment_dt FROM appointments a "
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
            b_end = b_dt + timedelta(minutes=b['duration_mins'])
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
    service_id = data.get('service_id')
    name = (data.get('customer_name') or '').strip()
    phone = (data.get('phone') or '').strip()
    apt_dt = data.get('appointment_dt')
    comment = (data.get('comment') or '').strip()

    if not all([service_id, name, phone, apt_dt]):
        return jsonify({'error': 'Missing required fields'}), 400

    db = get_db()
    svc = db.execute('SELECT * FROM services WHERE id=%s AND business_id=%s', (service_id, biz['id'])).fetchone()
    if not svc:
        db.close()
        return jsonify({'error': 'Service not found'}), 404

    db.execute(
        'INSERT INTO appointments (business_id, service_id, customer_name, phone, appointment_dt, comment) VALUES (%s,%s,%s,%s,%s,%s)',
        (biz['id'], service_id, name, phone, apt_dt, comment)
    )
    db.commit()
    db.close()
    return jsonify({'success': True, 'service': svc['name']})

from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from db import get_db
from datetime import datetime, timedelta
import threading
from blueprints.booking import send_sms, format_phone

dashboard_bp = Blueprint('dashboard', __name__, url_prefix='/dashboard')

@dashboard_bp.route('/')
@login_required
def index():
    db = get_db()
    today = datetime.now().strftime('%Y-%m-%d')
    now = datetime.now()
    hour = now.hour
    greeting = 'Good morning' if hour < 12 else ('Good afternoon' if hour < 17 else 'Good evening')

    today_apts = db.execute(
        "SELECT a.*, s.name as service_name, s.duration_mins, s.price "
        "FROM appointments a JOIN services s ON a.service_id=s.id "
        "WHERE a.business_id=%s AND a.appointment_dt LIKE %s AND a.status='confirmed' "
        "ORDER BY a.appointment_dt",
        (current_user.id, f'{today}%')
    ).fetchall()

    week_start = (now - timedelta(days=now.weekday())).strftime('%Y-%m-%d')
    week_end = (now + timedelta(days=6 - now.weekday())).strftime('%Y-%m-%d')
    week_count = db.execute(
        "SELECT COUNT(*) FROM appointments WHERE business_id=%s AND status='confirmed' "
        "AND appointment_dt >= %s AND appointment_dt <= %s",
        (current_user.id, week_start, week_end + ' 23:59')
    ).fetchone()['count']

    total = db.execute(
        "SELECT COUNT(*) FROM appointments WHERE business_id=%s AND status='confirmed'",
        (current_user.id,)
    ).fetchone()['count']

    db.close()
    return render_template('dashboard/index.html',
        today_apts=today_apts, today_count=len(today_apts),
        week_count=week_count, total=total, greeting=greeting)

@dashboard_bp.route('/services')
@login_required
def services():
    db = get_db()
    svcs = db.execute(
        'SELECT * FROM services WHERE business_id=%s ORDER BY sort_order, id',
        (current_user.id,)
    ).fetchall()
    db.close()
    return render_template('dashboard/services.html', services=svcs)

@dashboard_bp.route('/services/add', methods=['POST'])
@login_required
def add_service():
    name = request.form.get('name', '').strip()
    name_sub = request.form.get('name_sub', '').strip()
    duration = int(request.form.get('duration', 30))
    price_str = request.form.get('price', '').strip()
    price = float(price_str) if price_str else None

    if not name:
        flash('Service name is required.', 'error')
        return redirect(url_for('dashboard.services'))

    db = get_db()
    db.execute(
        'INSERT INTO services (business_id, name, name_sub, duration_mins, price) VALUES (%s,%s,%s,%s,%s)',
        (current_user.id, name, name_sub, duration, price)
    )
    db.commit()
    db.close()
    flash('Service added.', 'success')
    return redirect(url_for('dashboard.services'))

@dashboard_bp.route('/services/<int:svc_id>/delete', methods=['POST'])
@login_required
def delete_service(svc_id):
    db = get_db()
    db.execute('DELETE FROM services WHERE id=%s AND business_id=%s', (svc_id, current_user.id))
    db.commit()
    db.close()
    return redirect(url_for('dashboard.services'))

@dashboard_bp.route('/hours', methods=['GET', 'POST'])
@login_required
def hours():
    db = get_db()
    day_keys = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']
    day_names = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']

    if request.method == 'POST':
        for i, key in enumerate(day_keys):
            open_t = request.form.get(f'{key}_open', '09:00')
            close_t = request.form.get(f'{key}_close', '18:00')
            closed = 1 if request.form.get(f'{key}_closed') else 0
            db.execute(
                '''INSERT INTO business_hours (business_id, weekday, open_time, close_time, is_closed)
                   VALUES (%s,%s,%s,%s,%s)
                   ON CONFLICT (business_id, weekday)
                   DO UPDATE SET open_time=EXCLUDED.open_time,
                                 close_time=EXCLUDED.close_time,
                                 is_closed=EXCLUDED.is_closed''',
                (current_user.id, i, open_t, close_t, closed)
            )
        db.commit()
        flash('Hours updated.', 'success')

    rows = db.execute(
        'SELECT * FROM business_hours WHERE business_id=%s ORDER BY weekday',
        (current_user.id,)
    ).fetchall()
    db.close()

    hours_map = {r['weekday']: dict(r) for r in rows}
    days = [{'key': day_keys[i], 'name': day_names[i], 'data': hours_map.get(i, {})} for i in range(7)]
    return render_template('dashboard/hours.html', days=days)

@dashboard_bp.route('/appointments')
@login_required
def appointments():
    f = request.args.get('filter', 'upcoming')
    db = get_db()
    now = datetime.now().strftime('%Y-%m-%d %H:%M')

    base = ("SELECT a.*, s.name as service_name, s.name_sub, s.duration_mins, s.price "
            "FROM appointments a JOIN services s ON a.service_id=s.id WHERE a.business_id=%s")

    if f == 'upcoming':
        rows = db.execute(base + " AND a.status='confirmed' AND a.appointment_dt >= %s ORDER BY a.appointment_dt ASC",
                          (current_user.id, now)).fetchall()
    elif f == 'past':
        rows = db.execute(base + " AND a.status='confirmed' AND a.appointment_dt < %s ORDER BY a.appointment_dt DESC",
                          (current_user.id, now)).fetchall()
    elif f == 'cancelled':
        rows = db.execute(base + " AND a.status='cancelled' ORDER BY a.appointment_dt DESC",
                          (current_user.id,)).fetchall()
    else:
        rows = db.execute(base + " ORDER BY a.appointment_dt DESC", (current_user.id,)).fetchall()

    db.close()
    return render_template('dashboard/appointments.html', appointments=rows, current_filter=f)

@dashboard_bp.route('/appointments/<int:apt_id>/cancel', methods=['POST'])
@login_required
def cancel_appointment(apt_id):
    db = get_db()
    row = db.execute(
        "SELECT a.*, s.name as service_name FROM appointments a "
        "JOIN services s ON a.service_id=s.id "
        "WHERE a.id=%s AND a.business_id=%s",
        (apt_id, current_user.id)
    ).fetchone()
    db.execute("UPDATE appointments SET status='cancelled' WHERE id=%s AND business_id=%s",
               (apt_id, current_user.id))
    db.commit()
    db.close()

    if row:
        try:
            dt = datetime.strptime(row['appointment_dt'], '%Y-%m-%d %H:%M')
            dt_display = dt.strftime('%b %-d at %-I:%M %p')
        except Exception:
            dt_display = row['appointment_dt']
        biz_name = current_user.name
        biz_phone = current_user.phone or ''
        message = (
            f"Hi {row['customer_name']}, your appointment at {biz_name} has been cancelled.\n\n"
            f"Service: {row['service_name']}\n"
            f"Time: {dt_display}\n\n"
            + (f"To rebook, call {biz_phone}" if biz_phone else "Please rebook at your convenience.")
        )
        threading.Thread(target=send_sms, args=(format_phone(row['phone']), message), daemon=True).start()

    flash('Appointment cancelled.', 'success')
    return redirect(url_for('dashboard.appointments'))

@dashboard_bp.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    db = get_db()
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        address = request.form.get('address', '').strip()
        phone = request.form.get('phone', '').strip()
        description = request.form.get('description', '').strip()
        if name:
            db.execute(
                'UPDATE businesses SET name=%s, address=%s, phone=%s, description=%s WHERE id=%s',
                (name, address, phone, description, current_user.id)
            )
            db.commit()
            flash('Settings saved.', 'success')

    biz = db.execute('SELECT * FROM businesses WHERE id=%s', (current_user.id,)).fetchone()
    db.close()
    from flask import url_for
    booking_url = url_for('booking.book_page', slug=biz['slug'], _external=True)
    return render_template('dashboard/settings.html', biz=biz, booking_url=booking_url)

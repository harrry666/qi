from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from db import get_db, normalize_phone
from datetime import datetime, timedelta
import threading
import os
import uuid
import csv
import io
import json
from blueprints.booking import send_sms, format_phone
from blueprints.auth import CATEGORIES
from cloud import upload_to_cloudinary as _upload_to_cloudinary
from translations import t
from flask import g

dashboard_bp = Blueprint('dashboard', __name__, url_prefix='/dashboard')

@dashboard_bp.before_request
def _gate_subscription():
    if not current_user.is_authenticated or not hasattr(current_user, 'subscription_status'):
        return
    if request.endpoint == 'dashboard.billing':
        return
    from billing import sub_state
    if not sub_state(current_user)['has_access']:
        return redirect(url_for('dashboard.billing'))

_WEEKDAYS = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
_WEEKDAYS_EN = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

@dashboard_bp.app_template_filter('fmt_dt')
def fmt_dt(value):
    if not value:
        return '—'
    try:
        dt = datetime.strptime(value[:16], '%Y-%m-%d %H:%M')
    except (ValueError, TypeError):
        return value
    if getattr(g, 'lang', 'zh') == 'en':
        return f"{dt.strftime('%b')} {dt.day}, {_WEEKDAYS_EN[dt.weekday()]} {dt.hour:02d}:{dt.minute:02d}"
    return f"{dt.month}月{dt.day}日 {_WEEKDAYS[dt.weekday()]} {dt.hour:02d}:{dt.minute:02d}"

@dashboard_bp.route('/')
@login_required
def index():
    db = get_db()
    today = datetime.now().strftime('%Y-%m-%d')
    now = datetime.now()
    hour = now.hour
    greeting = t('dash.index.greeting_morning') if hour < 12 else (t('dash.index.greeting_afternoon') if hour < 17 else t('dash.index.greeting_evening'))

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

    last_week_start = (now - timedelta(days=now.weekday() + 7)).strftime('%Y-%m-%d')
    last_week_end = (now - timedelta(days=now.weekday() + 1)).strftime('%Y-%m-%d')
    last_week_count = db.execute(
        "SELECT COUNT(*) FROM appointments WHERE business_id=%s AND status='confirmed' "
        "AND appointment_dt >= %s AND appointment_dt <= %s",
        (current_user.id, last_week_start, last_week_end + ' 23:59')
    ).fetchone()['count']

    peak = db.execute(
        "SELECT SUBSTRING(appointment_dt, 12, 2) AS hh, COUNT(*) AS c "
        "FROM appointments WHERE business_id=%s AND status='confirmed' "
        "GROUP BY hh ORDER BY c DESC LIMIT 1",
        (current_user.id,)
    ).fetchone()
    db.close()

    delta = week_count - last_week_count
    if week_count == 0:
        insight = t('dash.index.insight_none')
    elif last_week_count == 0:
        insight = t('dash.index.insight_first', week_count=week_count)
    elif delta > 0:
        insight = t('dash.index.insight_up', delta=delta)
    elif delta < 0:
        insight = t('dash.index.insight_down', delta=-delta)
    else:
        insight = t('dash.index.insight_flat', week_count=week_count)
    if peak and peak['hh'] is not None:
        insight += t('dash.index.insight_peak', hour=int(peak['hh']))

    return render_template('dashboard/index.html',
        today_apts=today_apts, today_count=len(today_apts),
        week_count=week_count, total=total, greeting=greeting, now=now, insight=insight)

@dashboard_bp.route('/analytics')
@login_required
def analytics():
    db = get_db()
    now = datetime.now()
    this_month = now.strftime('%Y-%m')
    last_month_dt = (now.replace(day=1) - timedelta(days=1))
    last_month = last_month_dt.strftime('%Y-%m')
    since_30 = (now - timedelta(days=29)).strftime('%Y-%m-%d')

    rev_row = db.execute(
        "SELECT "
        "SUM(CASE WHEN SUBSTRING(a.appointment_dt, 1, 7) = %s THEN s.price ELSE 0 END) AS rev_this_month, "
        "SUM(CASE WHEN SUBSTRING(a.appointment_dt, 1, 7) = %s THEN s.price ELSE 0 END) AS rev_last_month, "
        "SUM(s.price) AS rev_alltime "
        "FROM appointments a JOIN services s ON a.service_id = s.id "
        "WHERE a.business_id = %s AND a.status = 'confirmed' AND s.price IS NOT NULL",
        (this_month, last_month, current_user.id)
    ).fetchone()
    rev_this  = float(rev_row['rev_this_month'] or 0)
    rev_last  = float(rev_row['rev_last_month'] or 0)
    rev_total = float(rev_row['rev_alltime'] or 0)
    rev_delta = rev_this - rev_last

    # Total appointment counts this month / last month / all time
    cnt_row = db.execute(
        "SELECT "
        "SUM(CASE WHEN SUBSTRING(a.appointment_dt, 1, 7) = %s THEN 1 ELSE 0 END) AS cnt_this_month, "
        "SUM(CASE WHEN SUBSTRING(a.appointment_dt, 1, 7) = %s THEN 1 ELSE 0 END) AS cnt_last_month, "
        "COUNT(*) AS cnt_alltime "
        "FROM appointments a "
        "WHERE a.business_id = %s AND a.status = 'confirmed'",
        (this_month, last_month, current_user.id)
    ).fetchone()
    cnt_this  = int(cnt_row['cnt_this_month'] or 0)
    cnt_last  = int(cnt_row['cnt_last_month'] or 0)
    cnt_total = int(cnt_row['cnt_alltime'] or 0)
    cnt_delta = cnt_this - cnt_last

    # Count how many confirmed appointments have no price (for the disclaimer)
    no_price_count = db.execute(
        "SELECT COUNT(*) FROM appointments a JOIN services s ON a.service_id=s.id "
        "WHERE a.business_id=%s AND a.status='confirmed' AND (s.price IS NULL OR s.price = 0)",
        (current_user.id,)
    ).fetchone()['count']

    daily_rows = db.execute(
        "SELECT SUBSTRING(appointment_dt, 1, 10) AS day, COUNT(*) AS cnt "
        "FROM appointments WHERE business_id = %s AND status = 'confirmed' AND appointment_dt >= %s "
        "GROUP BY day ORDER BY day",
        (current_user.id, since_30)
    ).fetchall()
    daily_map = {r['day']: r['cnt'] for r in daily_rows}
    daily_labels = [(now - timedelta(days=29 - i)).strftime('%m/%d') for i in range(30)]
    daily_full   = [(now - timedelta(days=29 - i)).strftime('%Y-%m-%d') for i in range(30)]
    daily_values = [daily_map.get(d, 0) for d in daily_full]

    top_svcs = db.execute(
        "SELECT s.name, COUNT(*) AS cnt FROM appointments a JOIN services s ON a.service_id = s.id "
        "WHERE a.business_id = %s AND a.status = 'confirmed' GROUP BY s.name ORDER BY cnt DESC LIMIT 6",
        (current_user.id,)
    ).fetchall()

    hour_rows = db.execute(
        "SELECT CAST(SUBSTRING(appointment_dt, 12, 2) AS INTEGER) AS hour, COUNT(*) AS cnt "
        "FROM appointments WHERE business_id = %s AND status = 'confirmed' GROUP BY hour ORDER BY hour",
        (current_user.id,)
    ).fetchall()
    hour_map = {r['hour']: r['cnt'] for r in hour_rows}
    hour_labels = [f'{h:02d}:00' for h in range(7, 22)]
    hour_values = [hour_map.get(h, 0) for h in range(7, 22)]
    peak_hour = max(hour_map, key=hour_map.get) if hour_map else None
    peak_hour_label = f'{peak_hour:02d}:00' if peak_hour is not None else '—'

    db.close()
    return render_template('dashboard/analytics.html',
        rev_this=rev_this, rev_last=rev_last, rev_total=rev_total, rev_delta=rev_delta,
        cnt_this=cnt_this, cnt_last=cnt_last, cnt_total=cnt_total, cnt_delta=cnt_delta,
        no_price_count=no_price_count,
        daily_labels=daily_labels, daily_values=daily_values,
        top_svc_labels=[r['name'] for r in top_svcs],
        top_svc_values=[r['cnt'] for r in top_svcs],
        hour_labels=hour_labels, hour_values=hour_values,
        peak_hour_label=peak_hour_label,
        this_month_label=now.strftime('%b %Y') if getattr(g, 'lang', 'zh') == 'en' else now.strftime('%Y年%-m月'),
        last_month_label=last_month_dt.strftime('%b %Y') if getattr(g, 'lang', 'zh') == 'en' else last_month_dt.strftime('%Y年%-m月'),
    )

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

def _parse_duration_range(form):
    """返回 (blocking_mins, display_min)。最长为主用于排档，最短仅用于显示。"""
    try:
        short = int(form.get('duration', 30) or 30)
    except ValueError:
        short = 30
    long_str = (form.get('duration_max', '') or '').strip()
    try:
        long_v = int(long_str) if long_str else None
    except ValueError:
        long_v = None
    if long_v and long_v > short:
        return long_v, short
    return short, None

@dashboard_bp.route('/services/add', methods=['POST'])
@login_required
def add_service():
    name = request.form.get('name', '').strip()
    name_sub = request.form.get('name_sub', '').strip()
    dur_mins, dur_min = _parse_duration_range(request.form)
    price_str = request.form.get('price', '').strip()
    price = float(price_str) if price_str else None
    emoji = request.form.get('emoji', '').strip()
    buffer_mins = int(request.form.get('buffer_mins', 0) or 0)
    icon_url = _upload_to_cloudinary(request.files.get('icon_image'), transformation=[{'width': 200, 'height': 200, 'crop': 'fill'}])

    if not name:
        flash('flash.svc.name_required', 'error')
        return redirect(url_for('dashboard.services'))

    db = get_db()
    db.execute(
        'INSERT INTO services (business_id, name, name_sub, duration_mins, duration_min_mins, price, emoji, buffer_mins, icon_url) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)',
        (current_user.id, name, name_sub, dur_mins, dur_min, price, emoji, buffer_mins, icon_url)
    )
    db.commit()
    db.close()
    flash('flash.svc.added', 'success')
    return redirect(url_for('dashboard.services'))

@dashboard_bp.route('/services/<int:svc_id>/icon', methods=['POST'])
@login_required
def update_service_icon(svc_id):
    icon_url = _upload_to_cloudinary(request.files.get('icon_image'), transformation=[{'width': 200, 'height': 200, 'crop': 'fill'}])
    if icon_url:
        db = get_db()
        db.execute(
            'UPDATE services SET icon_url=%s WHERE id=%s AND business_id=%s',
            (icon_url, svc_id, current_user.id)
        )
        db.commit()
        db.close()
        flash('flash.svc.icon_updated', 'success')
    else:
        flash('flash.svc.icon_invalid', 'error')
    return redirect(url_for('dashboard.services'))

@dashboard_bp.route('/services/<int:svc_id>/color', methods=['POST'])
@login_required
def update_service_color(svc_id):
    color = request.form.get('color', '').strip()
    db = get_db()
    db.execute(
        'UPDATE services SET color=%s WHERE id=%s AND business_id=%s',
        (color, svc_id, current_user.id)
    )
    db.commit()
    db.close()
    return ('', 204)

@dashboard_bp.route('/services/<int:svc_id>/edit', methods=['POST'])
@login_required
def edit_service(svc_id):
    name = request.form.get('name', '').strip()
    name_sub = request.form.get('name_sub', '').strip()
    dur_mins, dur_min = _parse_duration_range(request.form)
    price_str = request.form.get('price', '').strip()
    price = float(price_str) if price_str else None
    emoji = request.form.get('emoji', '').strip()
    buffer_mins = int(request.form.get('buffer_mins', 0) or 0)
    if not name:
        flash('flash.svc.name_required', 'error')
        return redirect(url_for('dashboard.services'))
    db = get_db()
    db.execute(
        'UPDATE services SET name=%s, name_sub=%s, duration_mins=%s, duration_min_mins=%s, price=%s, emoji=%s, buffer_mins=%s '
        'WHERE id=%s AND business_id=%s',
        (name, name_sub, dur_mins, dur_min, price, emoji, buffer_mins, svc_id, current_user.id)
    )
    db.commit()
    db.close()
    flash('flash.svc.updated', 'success')
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

    if request.method == 'POST':
        for i, key in enumerate(day_keys):
            open_t = request.form.get(f'{key}_open', '09:00')
            close_t = request.form.get(f'{key}_close', '18:00')
            closed = 0 if request.form.get(f'{key}_active') else 1
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
        flash('flash.hours.saved', 'success')

    rows = db.execute(
        'SELECT * FROM business_hours WHERE business_id=%s ORDER BY weekday',
        (current_user.id,)
    ).fetchall()
    db.close()

    hours_map = {r['weekday']: dict(r) for r in rows}
    days = [{'key': day_keys[i], 'name': t('weekday.' + day_keys[i]), 'data': hours_map.get(i, {})} for i in range(7)]
    return render_template('dashboard/hours.html', days=days)

PALETTE = ['#C9A84C', '#7A9E7E', '#B0785C', '#6E8CAE', '#A56CA8', '#C97A7A', '#7EA0A8', '#A89A5C',
           '#D48A4A', '#5C8A6E', '#8A6CA0', '#B85C7A', '#4A7A8A', '#A8785C', '#6E7AA8', '#8AA85C']

def _service_color(svc_id, svc_color):
    if svc_color:
        return svc_color
    return PALETTE[svc_id % len(PALETTE)]

@dashboard_bp.route('/calendar')
@login_required
def calendar():
    db = get_db()
    staff_rows = db.execute(
        'SELECT id, name FROM staff WHERE business_id=%s AND is_active=1 ORDER BY sort_order, id',
        (current_user.id,)
    ).fetchall()
    service_rows = db.execute(
        'SELECT id, name, duration_mins FROM services WHERE business_id=%s AND is_active=1 ORDER BY sort_order, id',
        (current_user.id,)
    ).fetchall()
    db.close()
    return render_template('dashboard/calendar.html', staff=staff_rows, services=service_rows)

@dashboard_bp.route('/calendar/events')
@login_required
def calendar_events():
    try:
        sf = int(request.args.get('staff_id') or 0) or None
    except ValueError:
        sf = None
    db = get_db()
    apt_sql = (
        "SELECT a.id, a.customer_name, a.appointment_dt, a.status, a.staff_id, a.comment, a.merchant_note, "
        "s.id as service_id, s.name as service_name, s.duration_mins, s.color as service_color, "
        "st.name as staff_name "
        "FROM appointments a JOIN services s ON a.service_id=s.id "
        "LEFT JOIN staff st ON a.staff_id=st.id "
        "WHERE a.business_id=%s AND a.status='confirmed'"
    )
    apt_params = [current_user.id]
    if sf:
        apt_sql += " AND a.staff_id=%s"
        apt_params.append(sf)
    rows = db.execute(apt_sql, tuple(apt_params)).fetchall()
    blk_sql = (
        "SELECT tb.id, tb.date, tb.start_time, tb.end_time, tb.reason, st.name AS staff_name "
        "FROM time_blocks tb LEFT JOIN staff st ON tb.staff_id=st.id "
        "WHERE tb.business_id=%s"
    )
    blk_params = [current_user.id]
    if sf:
        blk_sql += " AND (tb.staff_id=%s OR tb.staff_id IS NULL)"
        blk_params.append(sf)
    blocks = db.execute(blk_sql, tuple(blk_params)).fetchall()
    blackouts = db.execute(
        "SELECT start_date, end_date, reason FROM business_blackouts WHERE business_id=%s",
        (current_user.id,)
    ).fetchall()
    db.close()

    events = []
    for r in rows:
        try:
            start = datetime.strptime(r['appointment_dt'], '%Y-%m-%d %H:%M')
        except Exception:
            continue
        end = start + timedelta(minutes=r['duration_mins'] or 30)
        color = _service_color(r['service_id'], r['service_color'])
        title = r['customer_name'] or t('dash.calendar.unknown_customer')
        events.append({
            'id': r['id'],
            'title': title,
            'start': start.strftime('%Y-%m-%dT%H:%M:00'),
            'end': end.strftime('%Y-%m-%dT%H:%M:00'),
            'color': color,
            'extendedProps': {
                'type': 'appointment',
                'customer': r['customer_name'],
                'service': r['service_name'],
                'staff': r['staff_name'] or t('dash.calendar.any_staff'),
                'comment': r['comment'] or '',
                'note': r['merchant_note'] or '',
            }
        })

    for b in blocks:
        label = '🔒 ' + (b['reason'] or t('dash.calendar.blocked_label'))
        if b['staff_name']:
            label += f"（{b['staff_name']}）"
        events.append({
            'id': f"block-{b['id']}",
            'start': f"{b['date']}T{b['start_time']}:00",
            'end': f"{b['date']}T{b['end_time']}:00",
            'color': 'transparent',
            'textColor': '#5A4E42',
            'editable': False,
            'title': label,
            'extendedProps': {'type': 'block', 'blockId': b['id']},
        })

    for bo in blackouts:
        try:
            end_excl = (datetime.strptime(bo['end_date'], '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d')
        except Exception:
            end_excl = bo['end_date']
        events.append({
            'start': bo['start_date'],
            'end': end_excl,
            'allDay': True,
            'display': 'background',
            'color': 'transparent',
            'title': t('dash.calendar.blackout_label') + (bo['reason'] or ''),
            'extendedProps': {'type': 'blackout'},
        })

    return jsonify(events)

@dashboard_bp.route('/calendar/quick_block', methods=['POST'])
@login_required
def calendar_quick_block():
    date = request.form.get('date', '').strip()
    start_time = request.form.get('start_time', '').strip()
    end_time = request.form.get('end_time', '').strip()
    staff_id = request.form.get('staff_id', '').strip() or None
    reason = request.form.get('reason', '').strip()
    if not date or not start_time or not end_time or end_time <= start_time:
        return jsonify({'error': t('flash.calendar.invalid_time_range')}), 400
    db = get_db()
    if staff_id:
        own = db.execute('SELECT id FROM staff WHERE id=%s AND business_id=%s', (staff_id, current_user.id)).fetchone()
        if not own:
            staff_id = None
    db.execute(
        'INSERT INTO time_blocks (business_id, staff_id, date, start_time, end_time, reason) VALUES (%s,%s,%s,%s,%s,%s)',
        (current_user.id, staff_id, date, start_time, end_time, reason)
    )
    db.commit()
    db.close()
    return jsonify({'success': True})

@dashboard_bp.route('/calendar/quick_appointment', methods=['POST'])
@login_required
def calendar_quick_appointment():
    service_id = request.form.get('service_id', '').strip()
    staff_id = request.form.get('staff_id', '').strip() or None
    name = request.form.get('customer_name', '').strip()
    phone = normalize_phone(request.form.get('phone', '').strip())
    date = request.form.get('date', '').strip()
    time_ = request.form.get('time', '').strip()
    comment = request.form.get('comment', '').strip()
    if not all([service_id, name, phone, date, time_]):
        return jsonify({'error': t('flash.calendar.fill_all_fields')}), 400
    if len(phone) != 10:
        return jsonify({'error': t('flash.common.phone_invalid')}), 400
    db = get_db()
    svc = db.execute('SELECT id, name FROM services WHERE id=%s AND business_id=%s', (service_id, current_user.id)).fetchone()
    if not svc:
        db.close()
        return jsonify({'error': t('flash.calendar.service_not_found')}), 404
    if staff_id:
        own = db.execute('SELECT id FROM staff WHERE id=%s AND business_id=%s', (staff_id, current_user.id)).fetchone()
        if not own:
            staff_id = None
    apt_dt = f'{date} {time_}'
    from db import upsert_customer
    customer_id = upsert_customer(db, current_user.id, phone, name)
    cancel_token = str(uuid.uuid4())
    db.execute(
        'INSERT INTO appointments (business_id, service_id, customer_name, phone, appointment_dt, comment, cancel_token, staff_id, customer_id) '
        'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)',
        (current_user.id, service_id, name, phone, apt_dt, comment, cancel_token, staff_id, customer_id)
    )
    db.commit()
    db.close()

    try:
        dt_display = datetime.strptime(apt_dt, '%Y-%m-%d %H:%M').strftime('%Y年%-m月%-d日 %-H:%M')
    except ValueError:
        dt_display = apt_dt
    _base = os.environ.get('BASE_URL', request.host_url).rstrip('/')
    cancel_url = f"{_base}/cancel/{cancel_token}"
    biz_phone = current_user.phone or ''
    customer_msg = (
        f"【预约确认】{name}，您在【{current_user.name}】的预约已确认。\n\n"
        f"服务：{svc['name']}\n"
        f"时间：{dt_display}\n"
        + (f"如有疑问请致电：{biz_phone}\n" if biz_phone else '')
        + f"\n如需取消：{cancel_url}"
        + "\n或直接回复本短信「取消」"
    )
    threading.Thread(target=send_sms, args=(format_phone(phone), customer_msg), daemon=True).start()

    return jsonify({'success': True})

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
            dt_display = dt.strftime('%Y年%-m月%-d日 %-H:%M')
        except Exception:
            dt_display = row['appointment_dt']
        biz_name = current_user.name
        biz_phone = current_user.phone or ''
        message = (
            f"【预约取消】{row['customer_name']}，您在【{biz_name}】的预约已被取消。\n\n"
            f"服务：{row['service_name']}\n"
            f"时间：{dt_display}\n\n"
            + (f"如需重新预约请致电：{biz_phone}" if biz_phone else "如需重新预约请联系商家。")
        )
        threading.Thread(target=send_sms, args=(format_phone(row['phone']), message), daemon=True).start()
        if biz_phone:
            biz_msg = (
                f"【取消提醒】客人 {row['customer_name']} 的预约已取消。\n"
                f"服务：{row['service_name']}\n时间：{dt_display}"
            )
            threading.Thread(target=send_sms, args=(format_phone(biz_phone), biz_msg), daemon=True).start()

    flash('flash.apt.cancelled', 'success')
    return redirect(url_for('dashboard.appointments'))

@dashboard_bp.route('/appointments/<int:apt_id>/reschedule', methods=['POST'])
@login_required
def reschedule_appointment(apt_id):
    new_dt_raw = request.form.get('new_dt', '').strip()
    if not new_dt_raw:
        flash('flash.apt.pick_datetime', 'error')
        return redirect(url_for('dashboard.appointments'))
    try:
        new_dt = datetime.strptime(new_dt_raw, '%Y-%m-%dT%H:%M')
    except ValueError:
        flash('flash.apt.invalid_date', 'error')
        return redirect(url_for('dashboard.appointments'))
    new_dt_str = new_dt.strftime('%Y-%m-%d %H:%M')
    db = get_db()
    row = db.execute(
        "SELECT a.*, s.name as service_name FROM appointments a "
        "JOIN services s ON a.service_id=s.id WHERE a.id=%s AND a.business_id=%s",
        (apt_id, current_user.id)
    ).fetchone()
    db.execute(
        'UPDATE appointments SET appointment_dt=%s WHERE id=%s AND business_id=%s',
        (new_dt_str, apt_id, current_user.id)
    )
    db.commit()
    db.close()
    if row:
        try:
            old_disp = datetime.strptime(row['appointment_dt'], '%Y-%m-%d %H:%M').strftime('%Y年%-m月%-d日 %-H:%M')
        except Exception:
            old_disp = row['appointment_dt']
        new_disp = new_dt.strftime('%Y年%-m月%-d日 %-H:%M')
        biz_name = current_user.name
        biz_phone = current_user.phone or ''
        cust_msg = (
            f"【预约改期】{row['customer_name']}，您在【{biz_name}】的预约时间已更改。\n\n"
            f"服务：{row['service_name']}\n原时间：{old_disp}\n新时间：{new_disp}\n\n"
            + (f"如有疑问请致电：{biz_phone}" if biz_phone else "如有疑问请联系商家。")
        )
        threading.Thread(target=send_sms, args=(format_phone(row['phone']), cust_msg), daemon=True).start()
        if biz_phone:
            biz_msg = (
                f"【改期提醒】客人 {row['customer_name']} 的预约已改期。\n"
                f"服务：{row['service_name']}\n{old_disp} → {new_disp}"
            )
            threading.Thread(target=send_sms, args=(format_phone(biz_phone), biz_msg), daemon=True).start()
    flash('flash.apt.rescheduled', 'success')
    return redirect(url_for('dashboard.appointments'))

@dashboard_bp.route('/appointments/<int:apt_id>/note', methods=['POST'])
@login_required
def save_appointment_note(apt_id):
    note = request.form.get('note', '').strip()
    db = get_db()
    db.execute(
        'UPDATE appointments SET merchant_note=%s WHERE id=%s AND business_id=%s',
        (note, apt_id, current_user.id)
    )
    db.commit()
    db.close()
    return ('', 204)

@dashboard_bp.route('/blackouts')
@login_required
def blackouts():
    db = get_db()
    rows = db.execute(
        'SELECT * FROM business_blackouts WHERE business_id=%s ORDER BY start_date',
        (current_user.id,)
    ).fetchall()
    db.close()
    blackout_list = []
    date_fmt = '%b %-d' if getattr(g, 'lang', 'zh') == 'en' else '%-m月%-d日'
    for row in rows:
        d = dict(row)
        try:
            d['start_date_fmt'] = datetime.strptime(d['start_date'], '%Y-%m-%d').strftime(date_fmt)
            d['end_date_fmt'] = datetime.strptime(d['end_date'], '%Y-%m-%d').strftime(date_fmt)
        except Exception:
            d['start_date_fmt'] = d['start_date']
            d['end_date_fmt'] = d['end_date']
        blackout_list.append(d)
    return render_template('dashboard/blackouts.html', blackouts=blackout_list)

@dashboard_bp.route('/blackouts/add', methods=['POST'])
@login_required
def add_blackout():
    start = request.form.get('start_date', '').strip()
    end = request.form.get('end_date', '').strip()
    reason = request.form.get('reason', '').strip()
    if not start or not end or end < start:
        flash('flash.blackouts.invalid_range', 'error')
        return redirect(url_for('dashboard.blackouts'))
    db = get_db()
    db.execute(
        'INSERT INTO business_blackouts (business_id, start_date, end_date, reason) VALUES (%s,%s,%s,%s)',
        (current_user.id, start, end, reason)
    )
    db.commit()
    db.close()
    flash('flash.blackouts.added', 'success')
    return redirect(url_for('dashboard.blackouts'))

@dashboard_bp.route('/blackouts/<int:bo_id>/delete', methods=['POST'])
@login_required
def delete_blackout(bo_id):
    db = get_db()
    db.execute('DELETE FROM business_blackouts WHERE id=%s AND business_id=%s', (bo_id, current_user.id))
    db.commit()
    db.close()
    return redirect(url_for('dashboard.blackouts'))

@dashboard_bp.route('/blocks')
@login_required
def blocks():
    db = get_db()
    rows = db.execute(
        'SELECT tb.*, st.name AS staff_name FROM time_blocks tb '
        'LEFT JOIN staff st ON tb.staff_id=st.id '
        'WHERE tb.business_id=%s ORDER BY tb.date, tb.start_time',
        (current_user.id,)
    ).fetchall()
    staff = db.execute(
        'SELECT id, name FROM staff WHERE business_id=%s AND is_active=1 ORDER BY sort_order, id',
        (current_user.id,)
    ).fetchall()
    db.close()
    today = datetime.now().strftime('%Y-%m-%d')
    date_fmt = '%b %-d' if getattr(g, 'lang', 'zh') == 'en' else '%-m月%-d日'
    block_list = []
    for r in rows:
        d = dict(r)
        try:
            d['date_fmt'] = datetime.strptime(d['date'], '%Y-%m-%d').strftime(date_fmt)
        except Exception:
            d['date_fmt'] = d['date']
        block_list.append(d)
    return render_template('dashboard/blocks_time.html', blocks=block_list, staff=staff, today=today)

@dashboard_bp.route('/blocks/add', methods=['POST'])
@login_required
def add_block():
    start_date = request.form.get('start_date', '').strip()
    end_date = request.form.get('end_date', '').strip() or start_date
    start_time = request.form.get('start_time', '').strip()
    end_time = request.form.get('end_time', '').strip()
    reason = request.form.get('reason', '').strip()
    staff_id = request.form.get('staff_id', '').strip() or None
    if not start_date or not start_time or not end_time or end_time <= start_time or end_date < start_date:
        flash('flash.blocks.invalid_range', 'error')
        return redirect(url_for('dashboard.blocks'))
    db = get_db()
    if staff_id:
        own = db.execute('SELECT id FROM staff WHERE id=%s AND business_id=%s', (staff_id, current_user.id)).fetchone()
        if not own:
            staff_id = None
    d0 = datetime.strptime(start_date, '%Y-%m-%d').date()
    d1 = datetime.strptime(end_date, '%Y-%m-%d').date()
    if (d1 - d0).days > 60:
        db.close()
        flash('flash.blocks.range_too_long', 'error')
        return redirect(url_for('dashboard.blocks'))
    cur = d0
    while cur <= d1:
        db.execute(
            'INSERT INTO time_blocks (business_id, staff_id, date, start_time, end_time, reason) VALUES (%s,%s,%s,%s,%s,%s)',
            (current_user.id, staff_id, cur.strftime('%Y-%m-%d'), start_time, end_time, reason)
        )
        cur += timedelta(days=1)
    db.commit()
    db.close()
    flash('flash.blocks.locked', 'success')
    return redirect(url_for('dashboard.blocks'))

@dashboard_bp.route('/blocks/<int:bid>/delete', methods=['POST'])
@login_required
def delete_block(bid):
    db = get_db()
    db.execute('DELETE FROM time_blocks WHERE id=%s AND business_id=%s', (bid, current_user.id))
    db.commit()
    db.close()
    return redirect(url_for('dashboard.blocks'))

@dashboard_bp.route('/broadcast')
@login_required
def broadcast():
    db = get_db()
    customers = db.execute(
        "SELECT id, name, phone FROM customers "
        "WHERE business_id=%s AND phone IS NOT NULL AND phone != '' "
        "ORDER BY name",
        (current_user.id,)
    ).fetchall()
    requests_ = db.execute(
        "SELECT id, message, recipient_count, status, sent_count, created_at "
        "FROM broadcast_requests WHERE business_id=%s ORDER BY created_at DESC LIMIT 20",
        (current_user.id,)
    ).fetchall()
    db.close()
    return render_template('dashboard/broadcast.html', customers=customers, requests=requests_)

@dashboard_bp.route('/broadcast/send', methods=['POST'])
@login_required
def broadcast_send():
    message = (request.form.get('message') or '').strip()
    ids = request.form.getlist('customer_ids')
    if not message:
        flash('flash.broadcast.message_required', 'error')
        return redirect(url_for('dashboard.broadcast'))
    if not ids:
        flash('flash.broadcast.pick_customer', 'error')
        return redirect(url_for('dashboard.broadcast'))
    db = get_db()
    rows = db.execute(
        "SELECT phone FROM customers "
        "WHERE business_id=%s AND id = ANY(%s) AND phone IS NOT NULL AND phone != ''",
        (current_user.id, [int(i) for i in ids])
    ).fetchall()
    phones = [format_phone(r['phone']) for r in rows if r['phone']]
    if not phones:
        db.close()
        flash('flash.broadcast.no_valid_phones', 'error')
        return redirect(url_for('dashboard.broadcast'))
    db.execute(
        "INSERT INTO broadcast_requests (business_id, message, phones, recipient_count, status) "
        "VALUES (%s,%s,%s,%s,'pending')",
        (current_user.id, message, json.dumps(phones), len(phones))
    )
    db.commit()
    db.close()
    flash(t('flash.broadcast.submitted', n=len(phones)), 'success')
    return redirect(url_for('dashboard.broadcast'))

@dashboard_bp.route('/customers')
@login_required
def customers():
    db = get_db()
    rows = db.execute(
        "SELECT c.id, c.name, c.phone, c.avatar_url, c.balance, "
        "COUNT(a.id) as visit_count, MAX(a.appointment_dt) as last_visit, MIN(a.appointment_dt) as first_visit "
        "FROM customers c "
        "LEFT JOIN appointments a ON a.customer_id = c.id AND a.status='confirmed' "
        "WHERE c.business_id=%s "
        "GROUP BY c.id "
        "ORDER BY visit_count DESC, last_visit DESC NULLS LAST",
        (current_user.id,)
    ).fetchall()
    db.close()
    return render_template('dashboard/customers.html', customers=rows)

@dashboard_bp.route('/customers/add', methods=['POST'])
@login_required
def add_customer():
    from db import upsert_customer
    name = request.form.get('name', '').strip()
    phone = normalize_phone(request.form.get('phone', '').strip())
    preferences = request.form.get('preferences', '').strip()
    private_note = request.form.get('private_note', '').strip()
    balance = request.form.get('balance', '').strip()
    if not name or not phone:
        flash('flash.customers.name_phone_required', 'error')
        return redirect(url_for('dashboard.customers'))
    if len(phone) != 10:
        flash('flash.common.phone_invalid', 'error')
        return redirect(url_for('dashboard.customers'))
    db = get_db()
    existing = db.execute('SELECT id FROM customers WHERE business_id=%s AND phone=%s', (current_user.id, phone)).fetchone()
    if existing:
        db.close()
        flash('flash.customers.phone_exists', 'error')
        return redirect(url_for('dashboard.customer_detail', cid=existing['id']))
    cid = upsert_customer(db, current_user.id, phone, name)
    try:
        balance_val = int(balance) if balance else 0
    except ValueError:
        balance_val = 0
    db.execute(
        'UPDATE customers SET preferences=%s, private_note=%s, balance=%s WHERE id=%s',
        (preferences, private_note, balance_val, cid)
    )
    if balance_val:
        db.execute('INSERT INTO balance_transactions (customer_id, delta, reason) VALUES (%s,%s,%s)',
                   (cid, balance_val, t('flash.customers.initial_balance_reason')))
    db.commit()
    db.close()
    flash('flash.customers.added', 'success')
    return redirect(url_for('dashboard.customer_detail', cid=cid))

@dashboard_bp.route('/customers/import', methods=['POST'])
@login_required
def import_customers():
    from db import upsert_customer
    file = request.files.get('csv_file')
    if not file or not file.filename:
        flash('flash.customers.csv_required', 'error')
        return redirect(url_for('dashboard.customers'))
    try:
        content = file.stream.read().decode('utf-8-sig')
    except UnicodeDecodeError:
        flash('flash.customers.csv_encoding', 'error')
        return redirect(url_for('dashboard.customers'))
    rows = list(csv.reader(io.StringIO(content)))
    if rows and rows[0] and not any(ch.isdigit() for ch in rows[0][1] if len(rows[0]) > 1):
        rows = rows[1:]
    db = get_db()
    added, updated, skipped = 0, 0, 0
    for row in rows:
        row = [c.strip() for c in row]
        if len(row) < 2 or not row[1]:
            skipped += 1
            continue
        name = row[0]
        phone = row[1]
        preferences = row[2] if len(row) > 2 else ''
        try:
            balance_val = int(row[3]) if len(row) > 3 and row[3] else 0
        except ValueError:
            balance_val = 0
        existing = db.execute('SELECT id FROM customers WHERE business_id=%s AND phone=%s', (current_user.id, phone)).fetchone()
        cid = upsert_customer(db, current_user.id, phone, name)
        db.execute(
            'UPDATE customers SET preferences=%s, balance=%s WHERE id=%s',
            (preferences, balance_val, cid)
        )
        if existing:
            updated += 1
        else:
            added += 1
            if balance_val:
                db.execute('INSERT INTO balance_transactions (customer_id, delta, reason) VALUES (%s,%s,%s)',
                           (cid, balance_val, t('flash.customers.import_balance_reason')))
    db.commit()
    db.close()
    flash(t('flash.customers.import_done', added=added, updated=updated, skipped=skipped), 'success')
    return redirect(url_for('dashboard.customers'))

@dashboard_bp.route('/customers/<int:cid>')
@login_required
def customer_detail(cid):
    db = get_db()
    cust = db.execute('SELECT * FROM customers WHERE id=%s AND business_id=%s', (cid, current_user.id)).fetchone()
    if not cust:
        db.close()
        flash('flash.customers.not_found', 'error')
        return redirect(url_for('dashboard.customers'))
    visits = db.execute(
        "SELECT a.*, s.name as service_name FROM appointments a JOIN services s ON a.service_id=s.id "
        "WHERE a.customer_id=%s ORDER BY a.appointment_dt DESC LIMIT 20",
        (cid,)
    ).fetchall()
    photos = db.execute(
        "SELECT * FROM customer_photos WHERE customer_id=%s ORDER BY created_at DESC",
        (cid,)
    ).fetchall()
    transactions = db.execute(
        "SELECT * FROM balance_transactions WHERE customer_id=%s ORDER BY created_at DESC LIMIT 20",
        (cid,)
    ).fetchall()
    db.close()
    return render_template('dashboard/customer_detail.html', c=cust, visits=visits, photos=photos, transactions=transactions)

@dashboard_bp.route('/customers/<int:cid>/profile', methods=['POST'])
@login_required
def customer_update_profile(cid):
    name = request.form.get('name', '').strip()
    phone = normalize_phone(request.form.get('phone', '').strip())
    preferences = request.form.get('preferences', '').strip()
    private_note = request.form.get('private_note', '').strip()
    if phone and len(phone) != 10:
        flash('flash.common.phone_invalid', 'error')
        return redirect(url_for('dashboard.customer_detail', cid=cid))
    db = get_db()
    if phone:
        clash = db.execute(
            'SELECT id FROM customers WHERE business_id=%s AND phone=%s AND id!=%s',
            (current_user.id, phone, cid)
        ).fetchone()
        if clash:
            db.close()
            flash('flash.customers.phone_taken', 'error')
            return redirect(url_for('dashboard.customer_detail', cid=cid))
    db.execute(
        'UPDATE customers SET name=%s, phone=%s, preferences=%s, private_note=%s WHERE id=%s AND business_id=%s',
        (name, phone, preferences, private_note, cid, current_user.id)
    )
    db.commit()
    db.close()
    flash('flash.customers.profile_saved', 'success')
    return redirect(url_for('dashboard.customer_detail', cid=cid))

@dashboard_bp.route('/customers/<int:cid>/delete', methods=['POST'])
@login_required
def customer_delete(cid):
    db = get_db()
    own = db.execute('SELECT id FROM customers WHERE id=%s AND business_id=%s', (cid, current_user.id)).fetchone()
    if not own:
        db.close()
        flash('flash.customers.not_found', 'error')
        return redirect(url_for('dashboard.customers'))
    db.execute('DELETE FROM customer_photos WHERE customer_id=%s', (cid,))
    db.execute('DELETE FROM balance_transactions WHERE customer_id=%s', (cid,))
    db.execute('UPDATE appointments SET customer_id=NULL WHERE customer_id=%s AND business_id=%s', (cid, current_user.id))
    db.execute('DELETE FROM customers WHERE id=%s AND business_id=%s', (cid, current_user.id))
    db.commit()
    db.close()
    flash('flash.customers.deleted', 'success')
    return redirect(url_for('dashboard.customers'))

@dashboard_bp.route('/customers/<int:cid>/avatar', methods=['POST'])
@login_required
def customer_update_avatar(cid):
    avatar_url = _upload_to_cloudinary(request.files.get('avatar'), folder='qi/customers', transformation=[{'width': 300, 'height': 300, 'crop': 'fill'}])
    if avatar_url:
        db = get_db()
        db.execute('UPDATE customers SET avatar_url=%s WHERE id=%s AND business_id=%s', (avatar_url, cid, current_user.id))
        db.commit()
        db.close()
        flash('flash.customers.avatar_updated', 'success')
    else:
        flash('flash.customers.avatar_invalid', 'error')
    return redirect(url_for('dashboard.customer_detail', cid=cid))

@dashboard_bp.route('/customers/<int:cid>/photo', methods=['POST'])
@login_required
def customer_add_photo(cid):
    photo_url = _upload_to_cloudinary(request.files.get('photo'), folder='qi/customer_photos')
    note = request.form.get('note', '').strip()
    if photo_url:
        db = get_db()
        cust = db.execute('SELECT id FROM customers WHERE id=%s AND business_id=%s', (cid, current_user.id)).fetchone()
        if cust:
            db.execute(
                "INSERT INTO customer_photos (customer_id, photo_url, note, uploaded_by) VALUES (%s,%s,%s,'merchant')",
                (cid, photo_url, note)
            )
            db.commit()
            flash('flash.customers.photo_added', 'success')
        db.close()
    else:
        flash('flash.customers.avatar_invalid', 'error')
    return redirect(url_for('dashboard.customer_detail', cid=cid))

@dashboard_bp.route('/customers/<int:cid>/photo/<int:pid>/delete', methods=['POST'])
@login_required
def customer_delete_photo(cid, pid):
    db = get_db()
    db.execute(
        "DELETE FROM customer_photos WHERE id=%s AND customer_id=%s "
        "AND customer_id IN (SELECT id FROM customers WHERE business_id=%s)",
        (pid, cid, current_user.id)
    )
    db.commit()
    db.close()
    return redirect(url_for('dashboard.customer_detail', cid=cid))

@dashboard_bp.route('/customers/<int:cid>/balance', methods=['POST'])
@login_required
def customer_adjust_balance(cid):
    try:
        delta = int(request.form.get('delta', '0'))
    except ValueError:
        delta = 0
    reason = request.form.get('reason', '').strip()
    if delta:
        db = get_db()
        cust = db.execute('SELECT id FROM customers WHERE id=%s AND business_id=%s', (cid, current_user.id)).fetchone()
        if cust:
            db.execute('UPDATE customers SET balance = balance + %s WHERE id=%s', (delta, cid))
            db.execute(
                'INSERT INTO balance_transactions (customer_id, delta, reason) VALUES (%s,%s,%s)',
                (cid, delta, reason)
            )
            db.commit()
            flash('flash.customers.balance_updated', 'success')
        db.close()
    return redirect(url_for('dashboard.customer_detail', cid=cid))

@dashboard_bp.route('/billing')
@login_required
def billing():
    from billing import PLAN_PRICE
    return render_template('dashboard/billing.html', price=PLAN_PRICE)

@dashboard_bp.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    db = get_db()
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        address = request.form.get('address', '').strip()
        phone = normalize_phone(request.form.get('phone', '').strip())
        description = request.form.get('description', '').strip()
        category = request.form.get('category', '').strip()
        support_contact = request.form.get('support_contact', '').strip()

        if len(phone) != 10:
            flash('flash.common.phone_invalid', 'error')
            return redirect(url_for('dashboard.settings'))

        avatar_url = current_user.avatar_url or ''
        cover_url = current_user.cover_url or ''

        avatar_file = request.files.get('avatar')
        if avatar_file and avatar_file.filename:
            uploaded = _upload_to_cloudinary(avatar_file, folder='qi/avatars', transformation=[{'width': 400, 'height': 400, 'crop': 'fill'}])
            if uploaded:
                avatar_url = uploaded
            else:
                flash('flash.settings.avatar_failed', 'error')

        cover_file = request.files.get('cover')
        if cover_file and cover_file.filename:
            uploaded = _upload_to_cloudinary(cover_file, folder='qi/covers', transformation=[{'width': 1200, 'height': 400, 'crop': 'fill'}])
            if uploaded:
                cover_url = uploaded
            else:
                flash('flash.settings.cover_failed', 'error')

        if name:
            db.execute(
                'UPDATE businesses SET name=%s, address=%s, phone=%s, description=%s, category=%s, avatar_url=%s, cover_url=%s, support_contact=%s WHERE id=%s',
                (name, address, phone, description, category, avatar_url, cover_url, support_contact, current_user.id)
            )
            db.commit()
            flash('flash.settings.saved', 'success')

    biz = db.execute('SELECT * FROM businesses WHERE id=%s', (current_user.id,)).fetchone()
    if not biz['calendar_token']:
        token = uuid.uuid4().hex
        db.execute('UPDATE businesses SET calendar_token=%s WHERE id=%s', (token, current_user.id))
        db.commit()
        biz = db.execute('SELECT * FROM businesses WHERE id=%s', (current_user.id,)).fetchone()
    db.close()
    from flask import url_for
    _base = os.environ.get('BASE_URL', request.host_url).rstrip('/')
    booking_url = f"{_base}{url_for('booking.book_page', slug=biz['slug'])}"
    calendar_url = f"{_base}{url_for('booking.calendar_feed', token=biz['calendar_token'])}"
    return render_template('dashboard/settings.html', biz=biz, booking_url=booking_url, calendar_url=calendar_url, categories=CATEGORIES)

@dashboard_bp.route('/settings/calendar_token/regenerate', methods=['POST'])
@login_required
def regenerate_calendar_token():
    db = get_db()
    db.execute('UPDATE businesses SET calendar_token=%s WHERE id=%s', (uuid.uuid4().hex, current_user.id))
    db.commit()
    db.close()
    flash('flash.settings.calendar_regenerated', 'success')
    return redirect(url_for('dashboard.settings'))

@dashboard_bp.route('/feedback', methods=['GET', 'POST'])
@login_required
def feedback():
    if request.method == 'POST':
        message = request.form.get('message', '').strip()
        if message:
            db = get_db()
            db.execute(
                "INSERT INTO platform_feedback (source, business_id, name, contact, message) VALUES ('merchant',%s,%s,%s,%s)",
                (current_user.id, current_user.name, current_user.email, message)
            )
            db.commit()
            db.close()
            flash('flash.feedback.submitted', 'success')
        return redirect(url_for('dashboard.feedback'))
    return render_template('dashboard/feedback.html')

STAFF_DAY_KEYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']

@dashboard_bp.route('/staff')
@login_required
def staff():
    db = get_db()
    rows = db.execute(
        'SELECT * FROM staff WHERE business_id=%s ORDER BY sort_order, id',
        (current_user.id,)
    ).fetchall()
    all_services = db.execute(
        'SELECT * FROM services WHERE business_id=%s ORDER BY sort_order, id',
        (current_user.id,)
    ).fetchall()
    staff_list = []
    for r in rows:
        s = dict(r)
        svc_rows = db.execute(
            'SELECT service_id FROM staff_services WHERE staff_id=%s', (s['id'],)
        ).fetchall()
        s['service_ids'] = [sr['service_id'] for sr in svc_rows]
        hour_rows = db.execute(
            'SELECT * FROM staff_hours WHERE staff_id=%s', (s['id'],)
        ).fetchall()
        s['hours_map'] = {hr['weekday']: dict(hr) for hr in hour_rows}
        staff_list.append(s)
    db.close()
    days = [{'key': STAFF_DAY_KEYS[i], 'name': t('weekday.' + STAFF_DAY_KEYS[i]), 'weekday': i} for i in range(7)]
    return render_template('dashboard/staff.html', staff_list=staff_list, all_services=all_services, days=days)

@dashboard_bp.route('/staff/add', methods=['POST'])
@login_required
def add_staff():
    name = request.form.get('name', '').strip()
    emoji = request.form.get('emoji', '').strip()
    bio = request.form.get('bio', '').strip()
    if not name:
        flash('flash.staff.name_required', 'error')
        return redirect(url_for('dashboard.staff'))
    avatar_url = _upload_to_cloudinary(request.files.get('avatar'), folder='qi/staff', transformation=[{'width': 400, 'height': 400, 'crop': 'fill'}]) or ''
    db = get_db()
    db.execute(
        'INSERT INTO staff (business_id, name, emoji, avatar_url, bio) VALUES (%s,%s,%s,%s,%s)',
        (current_user.id, name, emoji, avatar_url, bio)
    )
    db.commit()
    db.close()
    flash('flash.staff.added', 'success')
    return redirect(url_for('dashboard.staff'))

@dashboard_bp.route('/staff/<int:sid>/edit', methods=['POST'])
@login_required
def edit_staff(sid):
    name = request.form.get('name', '').strip()
    emoji = request.form.get('emoji', '').strip()
    bio = request.form.get('bio', '').strip()
    if not name:
        flash('flash.staff.name_required', 'error')
        return redirect(url_for('dashboard.staff'))
    avatar_url = _upload_to_cloudinary(request.files.get('avatar'), folder='qi/staff', transformation=[{'width': 400, 'height': 400, 'crop': 'fill'}])
    db = get_db()
    if avatar_url:
        db.execute(
            'UPDATE staff SET name=%s, emoji=%s, bio=%s, avatar_url=%s WHERE id=%s AND business_id=%s',
            (name, emoji, bio, avatar_url, sid, current_user.id)
        )
    else:
        db.execute(
            'UPDATE staff SET name=%s, emoji=%s, bio=%s WHERE id=%s AND business_id=%s',
            (name, emoji, bio, sid, current_user.id)
        )
    db.commit()
    db.close()
    flash('flash.staff.updated', 'success')
    return redirect(url_for('dashboard.staff'))

@dashboard_bp.route('/staff/<int:sid>/delete', methods=['POST'])
@login_required
def delete_staff(sid):
    db = get_db()
    row = db.execute('SELECT id FROM staff WHERE id=%s AND business_id=%s', (sid, current_user.id)).fetchone()
    if row:
        db.execute('DELETE FROM staff_hours WHERE staff_id=%s', (sid,))
        db.execute('DELETE FROM staff_services WHERE staff_id=%s', (sid,))
        db.execute('DELETE FROM staff WHERE id=%s AND business_id=%s', (sid, current_user.id))
        db.commit()
    db.close()
    flash('flash.staff.deleted', 'success')
    return redirect(url_for('dashboard.staff'))

@dashboard_bp.route('/staff/<int:sid>/toggle', methods=['POST'])
@login_required
def toggle_staff(sid):
    db = get_db()
    db.execute(
        'UPDATE staff SET is_active = 1 - is_active WHERE id=%s AND business_id=%s',
        (sid, current_user.id)
    )
    db.commit()
    db.close()
    flash('flash.staff.status_updated', 'success')
    return redirect(url_for('dashboard.staff'))

@dashboard_bp.route('/staff/<int:sid>/services', methods=['POST'])
@login_required
def staff_services(sid):
    db = get_db()
    row = db.execute('SELECT id FROM staff WHERE id=%s AND business_id=%s', (sid, current_user.id)).fetchone()
    if not row:
        db.close()
        flash('flash.staff.not_found', 'error')
        return redirect(url_for('dashboard.staff'))
    service_ids = request.form.getlist('service_ids')
    db.execute('DELETE FROM staff_services WHERE staff_id=%s', (sid,))
    for svc_id in service_ids:
        valid = db.execute('SELECT id FROM services WHERE id=%s AND business_id=%s', (svc_id, current_user.id)).fetchone()
        if valid:
            db.execute('INSERT INTO staff_services (staff_id, service_id) VALUES (%s,%s) ON CONFLICT (staff_id, service_id) DO NOTHING', (sid, svc_id))
    db.commit()
    db.close()
    flash('flash.staff.services_updated', 'success')
    return redirect(url_for('dashboard.staff'))

@dashboard_bp.route('/staff/<int:sid>/hours', methods=['POST'])
@login_required
def staff_hours(sid):
    db = get_db()
    row = db.execute('SELECT id FROM staff WHERE id=%s AND business_id=%s', (sid, current_user.id)).fetchone()
    if not row:
        db.close()
        flash('flash.staff.not_found', 'error')
        return redirect(url_for('dashboard.staff'))
    for i, key in enumerate(STAFF_DAY_KEYS):
        open_t = request.form.get(f'{key}_open', '09:00')
        close_t = request.form.get(f'{key}_close', '18:00')
        closed = 0 if request.form.get(f'{key}_active') else 1
        db.execute(
            '''INSERT INTO staff_hours (staff_id, weekday, open_time, close_time, is_closed)
               VALUES (%s,%s,%s,%s,%s)
               ON CONFLICT (staff_id, weekday)
               DO UPDATE SET open_time=EXCLUDED.open_time,
                             close_time=EXCLUDED.close_time,
                             is_closed=EXCLUDED.is_closed''',
            (sid, i, open_t, close_t, closed)
        )
    db.commit()
    db.close()
    flash('flash.staff.hours_saved', 'success')
    return redirect(url_for('dashboard.staff'))

from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from db import get_db
from datetime import datetime, timedelta
import threading
import os
from blueprints.booking import send_sms, format_phone
from blueprints.auth import CATEGORIES
from cloud import upload_to_cloudinary as _upload_to_cloudinary

dashboard_bp = Blueprint('dashboard', __name__, url_prefix='/dashboard')

@dashboard_bp.route('/')
@login_required
def index():
    db = get_db()
    today = datetime.now().strftime('%Y-%m-%d')
    now = datetime.now()
    hour = now.hour
    greeting = '早上好' if hour < 12 else ('下午好' if hour < 17 else '晚上好')

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
        week_count=week_count, total=total, greeting=greeting, now=now)

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
        this_month_label=now.strftime('%Y年%-m月'),
        last_month_label=last_month_dt.strftime('%Y年%-m月'),
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

@dashboard_bp.route('/services/add', methods=['POST'])
@login_required
def add_service():
    name = request.form.get('name', '').strip()
    name_sub = request.form.get('name_sub', '').strip()
    duration = int(request.form.get('duration', 30))
    price_str = request.form.get('price', '').strip()
    price = float(price_str) if price_str else None
    emoji = request.form.get('emoji', '').strip()
    buffer_mins = int(request.form.get('buffer_mins', 0) or 0)
    icon_url = _upload_to_cloudinary(request.files.get('icon_image'), transformation=[{'width': 200, 'height': 200, 'crop': 'fill'}])

    if not name:
        flash('服务名称为必填项。', 'error')
        return redirect(url_for('dashboard.services'))

    db = get_db()
    db.execute(
        'INSERT INTO services (business_id, name, name_sub, duration_mins, price, emoji, buffer_mins, icon_url) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',
        (current_user.id, name, name_sub, duration, price, emoji, buffer_mins, icon_url)
    )
    db.commit()
    db.close()
    flash('服务已添加。', 'success')
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
        flash('图片已更新。', 'success')
    else:
        flash('请上传有效的图片文件（jpg/png/webp/gif）。', 'error')
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
    day_names = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']

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
        flash('营业时间已保存。', 'success')

    rows = db.execute(
        'SELECT * FROM business_hours WHERE business_id=%s ORDER BY weekday',
        (current_user.id,)
    ).fetchall()
    db.close()

    hours_map = {r['weekday']: dict(r) for r in rows}
    days = [{'key': day_keys[i], 'name': day_names[i], 'data': hours_map.get(i, {})} for i in range(7)]
    return render_template('dashboard/hours.html', days=days)

PALETTE = ['#C9A84C', '#7A9E7E', '#B0785C', '#6E8CAE', '#A56CA8', '#C97A7A', '#7EA0A8', '#A89A5C']

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
    db.close()
    return render_template('dashboard/calendar.html', staff=staff_rows)

@dashboard_bp.route('/calendar/events')
@login_required
def calendar_events():
    from flask import jsonify
    db = get_db()
    rows = db.execute(
        "SELECT a.id, a.customer_name, a.appointment_dt, a.status, a.staff_id, "
        "s.id as service_id, s.name as service_name, s.duration_mins, s.color as service_color, "
        "st.name as staff_name "
        "FROM appointments a JOIN services s ON a.service_id=s.id "
        "LEFT JOIN staff st ON a.staff_id=st.id "
        "WHERE a.business_id=%s AND a.status='confirmed'",
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
        title = r['service_name']
        if r['staff_name']:
            title += f" · {r['staff_name']}"
        events.append({
            'id': r['id'],
            'title': title,
            'start': start.strftime('%Y-%m-%dT%H:%M:00'),
            'end': end.strftime('%Y-%m-%dT%H:%M:00'),
            'color': color,
            'extendedProps': {
                'customer': r['customer_name'],
                'service': r['service_name'],
                'staff': r['staff_name'] or '不限员工',
            }
        })
    return jsonify(events)

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

    flash('预约已取消。', 'success')
    return redirect(url_for('dashboard.appointments'))

@dashboard_bp.route('/appointments/<int:apt_id>/reschedule', methods=['POST'])
@login_required
def reschedule_appointment(apt_id):
    new_dt_raw = request.form.get('new_dt', '').strip()
    if not new_dt_raw:
        flash('请选择新的日期和时间。', 'error')
        return redirect(url_for('dashboard.appointments'))
    try:
        new_dt = datetime.strptime(new_dt_raw, '%Y-%m-%dT%H:%M')
    except ValueError:
        flash('日期格式无效。', 'error')
        return redirect(url_for('dashboard.appointments'))
    if new_dt <= datetime.now():
        flash('改期时间不能是过去时间。', 'error')
        return redirect(url_for('dashboard.appointments'))
    new_dt_str = new_dt.strftime('%Y-%m-%d %H:%M')
    db = get_db()
    db.execute(
        'UPDATE appointments SET appointment_dt=%s WHERE id=%s AND business_id=%s',
        (new_dt_str, apt_id, current_user.id)
    )
    db.commit()
    db.close()
    flash('预约时间已更新。', 'success')
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
    for row in rows:
        d = dict(row)
        try:
            d['start_date_fmt'] = datetime.strptime(d['start_date'], '%Y-%m-%d').strftime('%-m月%-d日')
            d['end_date_fmt'] = datetime.strptime(d['end_date'], '%Y-%m-%d').strftime('%-m月%-d日')
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
        flash('日期范围无效。', 'error')
        return redirect(url_for('dashboard.blackouts'))
    db = get_db()
    db.execute(
        'INSERT INTO business_blackouts (business_id, start_date, end_date, reason) VALUES (%s,%s,%s,%s)',
        (current_user.id, start, end, reason)
    )
    db.commit()
    db.close()
    flash('休业期已添加。', 'success')
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
    block_list = []
    for r in rows:
        d = dict(r)
        try:
            d['date_fmt'] = datetime.strptime(d['date'], '%Y-%m-%d').strftime('%-m月%-d日')
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
        flash('日期或时间范围无效。', 'error')
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
        flash('日期范围不能超过 60 天。', 'error')
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
    flash('时段已锁定。', 'success')
    return redirect(url_for('dashboard.blocks'))

@dashboard_bp.route('/blocks/<int:bid>/delete', methods=['POST'])
@login_required
def delete_block(bid):
    db = get_db()
    db.execute('DELETE FROM time_blocks WHERE id=%s AND business_id=%s', (bid, current_user.id))
    db.commit()
    db.close()
    return redirect(url_for('dashboard.blocks'))

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

@dashboard_bp.route('/customers/<int:cid>')
@login_required
def customer_detail(cid):
    db = get_db()
    cust = db.execute('SELECT * FROM customers WHERE id=%s AND business_id=%s', (cid, current_user.id)).fetchone()
    if not cust:
        db.close()
        flash('未找到该客户。', 'error')
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
    preferences = request.form.get('preferences', '').strip()
    private_note = request.form.get('private_note', '').strip()
    db = get_db()
    db.execute(
        'UPDATE customers SET preferences=%s, private_note=%s WHERE id=%s AND business_id=%s',
        (preferences, private_note, cid, current_user.id)
    )
    db.commit()
    db.close()
    flash('客户档案已保存。', 'success')
    return redirect(url_for('dashboard.customer_detail', cid=cid))

@dashboard_bp.route('/customers/<int:cid>/avatar', methods=['POST'])
@login_required
def customer_update_avatar(cid):
    avatar_url = _upload_to_cloudinary(request.files.get('avatar'), folder='qi/customers', transformation=[{'width': 300, 'height': 300, 'crop': 'fill'}])
    if avatar_url:
        db = get_db()
        db.execute('UPDATE customers SET avatar_url=%s WHERE id=%s AND business_id=%s', (avatar_url, cid, current_user.id))
        db.commit()
        db.close()
        flash('头像已更新。', 'success')
    else:
        flash('请上传有效的图片文件。', 'error')
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
            flash('照片已添加。', 'success')
        db.close()
    else:
        flash('请上传有效的图片文件。', 'error')
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
            flash('余额已更新。', 'success')
        db.close()
    return redirect(url_for('dashboard.customer_detail', cid=cid))

@dashboard_bp.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    db = get_db()
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        address = request.form.get('address', '').strip()
        phone = request.form.get('phone', '').strip()
        description = request.form.get('description', '').strip()
        category = request.form.get('category', '').strip()
        support_contact = request.form.get('support_contact', '').strip()

        avatar_url = current_user.avatar_url or ''
        cover_url = current_user.cover_url or ''

        avatar_file = request.files.get('avatar')
        if avatar_file and avatar_file.filename:
            try:
                result = cloudinary.uploader.upload(avatar_file, folder='qi/avatars', transformation=[{'width': 400, 'height': 400, 'crop': 'fill'}])
                avatar_url = result['secure_url']
            except Exception as e:
                flash(f'头像上传失败: {e}', 'error')

        cover_file = request.files.get('cover')
        if cover_file and cover_file.filename:
            try:
                result = cloudinary.uploader.upload(cover_file, folder='qi/covers', transformation=[{'width': 1200, 'height': 400, 'crop': 'fill'}])
                cover_url = result['secure_url']
            except Exception as e:
                flash(f'封面上传失败: {e}', 'error')

        if name:
            db.execute(
                'UPDATE businesses SET name=%s, address=%s, phone=%s, description=%s, category=%s, avatar_url=%s, cover_url=%s, support_contact=%s WHERE id=%s',
                (name, address, phone, description, category, avatar_url, cover_url, support_contact, current_user.id)
            )
            db.commit()
            flash('设置已保存。', 'success')

    biz = db.execute('SELECT * FROM businesses WHERE id=%s', (current_user.id,)).fetchone()
    db.close()
    from flask import url_for
    booking_url = url_for('booking.book_page', slug=biz['slug'], _external=True)
    return render_template('dashboard/settings.html', biz=biz, booking_url=booking_url, categories=CATEGORIES)

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
            flash('反馈已提交，平台会尽快处理。', 'success')
        return redirect(url_for('dashboard.feedback'))
    return render_template('dashboard/feedback.html')

STAFF_DAY_KEYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']
STAFF_DAY_NAMES = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']

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
    days = [{'key': STAFF_DAY_KEYS[i], 'name': STAFF_DAY_NAMES[i], 'weekday': i} for i in range(7)]
    return render_template('dashboard/staff.html', staff_list=staff_list, all_services=all_services, days=days)

@dashboard_bp.route('/staff/add', methods=['POST'])
@login_required
def add_staff():
    name = request.form.get('name', '').strip()
    emoji = request.form.get('emoji', '').strip()
    bio = request.form.get('bio', '').strip()
    if not name:
        flash('员工姓名为必填项。', 'error')
        return redirect(url_for('dashboard.staff'))
    avatar_url = _upload_to_cloudinary(request.files.get('avatar'), folder='qi/staff', transformation=[{'width': 400, 'height': 400, 'crop': 'fill'}]) or ''
    db = get_db()
    db.execute(
        'INSERT INTO staff (business_id, name, emoji, avatar_url, bio) VALUES (%s,%s,%s,%s,%s)',
        (current_user.id, name, emoji, avatar_url, bio)
    )
    db.commit()
    db.close()
    flash('员工已添加。', 'success')
    return redirect(url_for('dashboard.staff'))

@dashboard_bp.route('/staff/<int:sid>/edit', methods=['POST'])
@login_required
def edit_staff(sid):
    name = request.form.get('name', '').strip()
    emoji = request.form.get('emoji', '').strip()
    bio = request.form.get('bio', '').strip()
    if not name:
        flash('员工姓名为必填项。', 'error')
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
    flash('员工信息已更新。', 'success')
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
    flash('员工已删除。', 'success')
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
    flash('员工状态已更新。', 'success')
    return redirect(url_for('dashboard.staff'))

@dashboard_bp.route('/staff/<int:sid>/services', methods=['POST'])
@login_required
def staff_services(sid):
    db = get_db()
    row = db.execute('SELECT id FROM staff WHERE id=%s AND business_id=%s', (sid, current_user.id)).fetchone()
    if not row:
        db.close()
        flash('员工不存在。', 'error')
        return redirect(url_for('dashboard.staff'))
    service_ids = request.form.getlist('service_ids')
    db.execute('DELETE FROM staff_services WHERE staff_id=%s', (sid,))
    for svc_id in service_ids:
        valid = db.execute('SELECT id FROM services WHERE id=%s AND business_id=%s', (svc_id, current_user.id)).fetchone()
        if valid:
            db.execute('INSERT INTO staff_services (staff_id, service_id) VALUES (%s,%s) ON CONFLICT (staff_id, service_id) DO NOTHING', (sid, svc_id))
    db.commit()
    db.close()
    flash('可做服务已更新。', 'success')
    return redirect(url_for('dashboard.staff'))

@dashboard_bp.route('/staff/<int:sid>/hours', methods=['POST'])
@login_required
def staff_hours(sid):
    db = get_db()
    row = db.execute('SELECT id FROM staff WHERE id=%s AND business_id=%s', (sid, current_user.id)).fetchone()
    if not row:
        db.close()
        flash('员工不存在。', 'error')
        return redirect(url_for('dashboard.staff'))
    for i, key in enumerate(STAFF_DAY_KEYS):
        open_t = request.form.get(f'{key}_open', '09:00')
        close_t = request.form.get(f'{key}_close', '18:00')
        closed = 1 if request.form.get(f'{key}_closed') else 0
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
    flash('排班已保存。', 'success')
    return redirect(url_for('dashboard.staff'))

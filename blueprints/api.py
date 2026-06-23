from flask import Blueprint, jsonify, request, url_for
from db import get_db
from datetime import datetime, timedelta
import os
import uuid
import threading
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

api_bp = Blueprint('api', __name__, url_prefix='/api')


def get_merchant_from_token():
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer '):
        return None
    token = auth_header[7:]
    db = get_db()
    try:
        biz = db.execute('SELECT * FROM businesses WHERE api_token=%s', (token,)).fetchone()
        return dict(biz) if biz else None
    finally:
        db.close()


def get_client_token():
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer '):
        return None
    return auth_header[7:]


def get_client_from_token():
    token = get_client_token()
    if not token:
        return None
    db = get_db()
    try:
        user = db.execute('SELECT * FROM users WHERE client_token=%s', (token,)).fetchone()
        return dict(user) if user else None
    finally:
        db.close()


def require_client():
    user = get_client_from_token()
    if not user:
        return None, (jsonify({'error': 'unauthorized'}), 401)
    return user, None


def require_merchant():
    biz = get_merchant_from_token()
    if not biz:
        return None, (jsonify({'error': 'unauthorized'}), 401)
    return biz, None


@api_bp.route('/businesses')
def list_businesses():
    db = get_db()
    try:
        rows = db.execute(
            'SELECT id, name, slug, category, description, address, avatar_url, cover_url, phone FROM businesses ORDER BY id'
        ).fetchall()
        return jsonify({'businesses': [dict(r) for r in rows]})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


@api_bp.route('/businesses/<slug>')
def get_business(slug):
    db = get_db()
    try:
        biz = db.execute('SELECT * FROM businesses WHERE slug=%s', (slug,)).fetchone()
        if not biz:
            return jsonify({'error': 'Not found'}), 404
        biz = dict(biz)
        svcs = db.execute(
            'SELECT * FROM services WHERE business_id=%s AND is_active=1 ORDER BY sort_order, id',
            (biz['id'],)
        ).fetchall()
        hours = db.execute(
            'SELECT * FROM business_hours WHERE business_id=%s ORDER BY weekday',
            (biz['id'],)
        ).fetchall()
        return jsonify({
            'business': {
                'id': biz['id'],
                'name': biz['name'],
                'slug': biz['slug'],
                'description': biz['description'],
                'address': biz['address'],
                'phone': biz['phone'],
                'category': biz['category'],
                'avatar_url': biz.get('avatar_url'),
                'cover_url': biz.get('cover_url'),
            },
            'services': [dict(s) for s in svcs],
            'hours': [dict(h) for h in hours],
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


@api_bp.route('/businesses/<slug>/slots')
def get_slots(slug):
    from blueprints.booking import generate_slots, filter_available
    date_str = request.args.get('date', '')
    service_id = request.args.get('service_id')
    db = get_db()
    try:
        biz = db.execute('SELECT * FROM businesses WHERE slug=%s', (slug,)).fetchone()
        if not biz:
            return jsonify({'error': 'Not found'}), 404
        biz = dict(biz)
        if not service_id:
            return jsonify({'slots': []})
        try:
            service_id = int(service_id)
        except (ValueError, TypeError):
            return jsonify({'slots': []})
        svc = db.execute(
            'SELECT * FROM services WHERE id=%s AND business_id=%s AND is_active=1',
            (service_id, biz['id'])
        ).fetchone()
        if not svc:
            return jsonify({'slots': []})
        svc = dict(svc)
        try:
            date_obj = datetime.strptime(date_str, '%Y-%m-%d').date()
        except (ValueError, TypeError):
            return jsonify({'slots': []})
        all_slots = generate_slots(biz['id'], date_obj, svc['duration_mins'])
        available = filter_available(biz['id'], date_str, all_slots, svc['duration_mins'])
        return jsonify({'slots': available})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


@api_bp.route('/bookings', methods=['POST'])
def create_booking():
    from blueprints.booking import send_sms, format_phone
    data = request.json or {}
    slug = (data.get('slug') or '').strip()
    service_id = data.get('service_id')
    customer_name = (data.get('customer_name') or '').strip()
    phone = (data.get('phone') or '').strip()
    date = (data.get('date') or '').strip()
    time = (data.get('time') or '').strip()
    comment = (data.get('comment') or '').strip()
    subscribe_authed = 1 if data.get('subscribe_authed') in (1, '1', True) else 0

    if not all([slug, service_id, customer_name, phone, date, time]):
        return jsonify({'error': '缺少必填字段'}), 400

    client = get_client_from_token()
    openid = client['openid'] if client else None

    db = get_db()
    try:
        biz = db.execute('SELECT * FROM businesses WHERE slug=%s', (slug,)).fetchone()
        if not biz:
            return jsonify({'error': '商家不存在'}), 400
        biz = dict(biz)

        svc = db.execute(
            'SELECT * FROM services WHERE id=%s AND business_id=%s AND is_active=1',
            (service_id, biz['id'])
        ).fetchone()
        if not svc:
            return jsonify({'error': '服务不存在'}), 400
        svc = dict(svc)

        appointment_dt = f'{date} {time}'
        try:
            apt_dt_obj = datetime.strptime(appointment_dt, '%Y-%m-%d %H:%M')
            if apt_dt_obj < datetime.now():
                return jsonify({'error': '不能预约过去的时间'}), 400
        except ValueError:
            return jsonify({'error': '日期时间格式无效'}), 400

        cancel_token = str(uuid.uuid4())
        db.execute(
            'INSERT INTO appointments (business_id, service_id, customer_name, phone, appointment_dt, comment, status, cancel_token, openid, subscribe_authed) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
            (biz['id'], service_id, customer_name, phone, appointment_dt, comment, 'confirmed', cancel_token, openid, subscribe_authed)
        )
        db.commit()

        try:
            dt_display = apt_dt_obj.strftime('%Y年%-m月%-d日 %-H:%M')
        except Exception:
            dt_display = appointment_dt

        biz_phone = biz.get('phone') or ''
        formatted_customer_phone = format_phone(phone)

        customer_msg = (
            f"【预约确认】{customer_name}，您在【{biz['name']}】的预约已确认。\n\n"
            f"服务：{svc['name']}\n"
            f"时间：{dt_display}\n"
            + (f"地址：{biz['address']}\n" if biz.get('address') else '')
            + (f"如有疑问请致电：{biz_phone}\n" if biz_phone else '')
        )
        threading.Thread(target=send_sms, args=(formatted_customer_phone, customer_msg), daemon=True).start()

        if biz_phone:
            owner_msg = (
                f"【新预约】{biz['name']}\n\n"
                f"客人：{customer_name}\n"
                f"电话：{phone}\n"
                f"服务：{svc['name']}\n"
                f"时间：{dt_display}\n"
                + (f"备注：{comment}" if comment else '')
            )
            threading.Thread(target=send_sms, args=(format_phone(biz_phone), owner_msg), daemon=True).start()

        return jsonify({'success': True, 'cancel_token': cancel_token, 'message': '预约成功'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


@api_bp.route('/bookings/<cancel_token>/cancel', methods=['POST'])
def cancel_booking(cancel_token):
    from blueprints.booking import send_sms, format_phone
    db = get_db()
    try:
        row = db.execute(
            "SELECT a.*, s.name as service_name, b.name as biz_name, b.phone as biz_phone "
            "FROM appointments a "
            "JOIN services s ON a.service_id=s.id "
            "JOIN businesses b ON a.business_id=b.id "
            "WHERE a.cancel_token=%s AND a.status='confirmed'",
            (cancel_token,)
        ).fetchone()
        if not row:
            return jsonify({'error': '预约不存在或已取消'}), 404
        row = dict(row)

        db.execute(
            "UPDATE appointments SET status='cancelled' WHERE cancel_token=%s AND status='confirmed'",
            (cancel_token,)
        )
        db.commit()

        try:
            dt = datetime.strptime(row['appointment_dt'], '%Y-%m-%d %H:%M')
            dt_display = dt.strftime('%Y年%-m月%-d日 %-H:%M')
        except Exception:
            dt_display = row['appointment_dt']

        if row.get('biz_phone'):
            msg = (
                f"【预约取消】{row['biz_name']}\n\n"
                f"客人：{row['customer_name']}\n"
                f"服务：{row['service_name']}\n"
                f"原定时间：{dt_display}"
            )
            threading.Thread(target=send_sms, args=(format_phone(row['biz_phone']), msg), daemon=True).start()

        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


@api_bp.route('/merchant/login', methods=['POST'])
def merchant_login():
    data = request.json or {}
    email = (data.get('email') or '').strip().lower()
    password = data.get('password') or ''
    if not email or not password:
        return jsonify({'error': '邮箱或密码错误'}), 401
    db = get_db()
    try:
        biz = db.execute('SELECT * FROM businesses WHERE email=%s', (email,)).fetchone()
        if not biz or not check_password_hash(biz['password_hash'], password):
            return jsonify({'error': '邮箱或密码错误'}), 401
        biz = dict(biz)
        token = str(uuid.uuid4())
        db.execute('UPDATE businesses SET api_token=%s WHERE id=%s', (token, biz['id']))
        db.commit()
        return jsonify({'token': token, 'business_name': biz['name'], 'id': biz['id']})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


@api_bp.route('/merchant/me')
def merchant_me():
    biz, err = require_merchant()
    if err:
        return err
    return jsonify({
        'id': biz['id'], 'name': biz['name'], 'email': biz['email'], 'slug': biz['slug'],
        'description': biz.get('description', ''), 'address': biz.get('address', ''),
        'phone': biz.get('phone', ''), 'avatar_url': biz.get('avatar_url', ''),
        'cover_url': biz.get('cover_url', ''),
    })


@api_bp.route('/merchant/appointments')
def merchant_appointments():
    biz, err = require_merchant()
    if err:
        return err
    date_str = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
    db = get_db()
    try:
        rows = db.execute(
            "SELECT a.*, s.name as service_name, s.duration_mins, s.price "
            "FROM appointments a JOIN services s ON a.service_id=s.id "
            "WHERE a.business_id=%s AND a.appointment_dt LIKE %s "
            "ORDER BY a.appointment_dt",
            (biz['id'], f'{date_str}%')
        ).fetchall()
        return jsonify({'appointments': [dict(r) for r in rows]})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


@api_bp.route('/merchant/appointments/list')
def merchant_appointments_list():
    biz, err = require_merchant()
    if err:
        return err
    filter_val = request.args.get('filter', 'all')
    today = datetime.now().strftime('%Y-%m-%d')
    db = get_db()
    try:
        sql = (
            "SELECT a.*, s.name as service_name, s.duration_mins, s.price "
            "FROM appointments a JOIN services s ON a.service_id=s.id "
            "WHERE a.business_id=%s"
        )
        params = [biz['id']]
        if filter_val == 'upcoming':
            sql += " AND SUBSTRING(a.appointment_dt, 1, 10) >= %s AND a.status != 'cancelled'"
            params.append(today)
        elif filter_val == 'past':
            sql += " AND SUBSTRING(a.appointment_dt, 1, 10) < %s"
            params.append(today)
        elif filter_val == 'cancelled':
            sql += " AND a.status = 'cancelled'"
        sql += " ORDER BY a.appointment_dt"
        rows = db.execute(sql, tuple(params)).fetchall()
        return jsonify({'appointments': [dict(r) for r in rows]})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


@api_bp.route('/merchant/appointments/<int:apt_id>/confirm', methods=['POST'])
def merchant_confirm_appointment(apt_id):
    biz, err = require_merchant()
    if err:
        return err
    db = get_db()
    try:
        row = db.execute(
            'SELECT business_id FROM appointments WHERE id=%s',
            (apt_id,)
        ).fetchone()
        if not row:
            return jsonify({'error': '预约不存在'}), 404
        if row['business_id'] != biz['id']:
            return jsonify({'error': 'unauthorized'}), 403
        db.execute(
            "UPDATE appointments SET status='confirmed' WHERE id=%s AND business_id=%s",
            (apt_id, biz['id'])
        )
        db.commit()
        return jsonify({'ok': True, 'status': 'confirmed'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


@api_bp.route('/merchant/appointments/<int:apt_id>/cancel', methods=['POST'])
def merchant_cancel_appointment(apt_id):
    from blueprints.booking import send_sms, format_phone
    biz, err = require_merchant()
    if err:
        return err
    db = get_db()
    try:
        row = db.execute(
            "SELECT a.*, s.name as service_name FROM appointments a "
            "JOIN services s ON a.service_id=s.id "
            "WHERE a.id=%s AND a.business_id=%s",
            (apt_id, biz['id'])
        ).fetchone()
        if not row:
            return jsonify({'error': '预约不存在'}), 404
        row = dict(row)

        db.execute(
            "UPDATE appointments SET status='cancelled' WHERE id=%s AND business_id=%s",
            (apt_id, biz['id'])
        )
        db.commit()

        try:
            dt = datetime.strptime(row['appointment_dt'], '%Y-%m-%d %H:%M')
            dt_display = dt.strftime('%Y年%-m月%-d日 %-H:%M')
        except Exception:
            dt_display = row['appointment_dt']

        biz_phone = biz.get('phone') or ''
        message = (
            f"【预约取消】{row['customer_name']}，您在【{biz['name']}】的预约已被取消。\n\n"
            f"服务：{row['service_name']}\n"
            f"时间：{dt_display}\n\n"
            + (f"如需重新预约请致电：{biz_phone}" if biz_phone else "如需重新预约请联系商家。")
        )
        threading.Thread(target=send_sms, args=(format_phone(row['phone']), message), daemon=True).start()

        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


@api_bp.route('/merchant/services', methods=['GET'])
def merchant_services():
    biz, err = require_merchant()
    if err:
        return err
    db = get_db()
    try:
        rows = db.execute(
            'SELECT * FROM services WHERE business_id=%s AND is_active=1 ORDER BY sort_order, id',
            (biz['id'],)
        ).fetchall()
        return jsonify({'services': [dict(r) for r in rows]})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


@api_bp.route('/merchant/services', methods=['POST'])
def merchant_add_service():
    biz, err = require_merchant()
    if err:
        return err
    data = request.json or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'error': '服务名称不能为空'}), 400
    name_sub = (data.get('name_sub') or '').strip()
    duration_mins = int(data.get('duration_mins', 30))
    price = data.get('price')
    if price is not None:
        price = float(price)
    emoji = (data.get('emoji') or '').strip()
    db = get_db()
    try:
        cur = db.execute(
            'INSERT INTO services (business_id, name, name_sub, duration_mins, price, emoji, is_active, sort_order) VALUES (%s,%s,%s,%s,%s,%s,1,0) RETURNING id',
            (biz['id'], name, name_sub, duration_mins, price, emoji)
        )
        new_id = cur.fetchone()['id']
        db.commit()
        return jsonify({'success': True, 'id': new_id})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


@api_bp.route('/merchant/services/<int:svc_id>', methods=['DELETE'])
def merchant_delete_service(svc_id):
    biz, err = require_merchant()
    if err:
        return err
    db = get_db()
    try:
        row = db.execute(
            'SELECT id FROM services WHERE id=%s AND business_id=%s',
            (svc_id, biz['id'])
        ).fetchone()
        if not row:
            return jsonify({'error': '服务不存在'}), 404
        db.execute(
            'UPDATE services SET is_active=0 WHERE id=%s AND business_id=%s',
            (svc_id, biz['id'])
        )
        db.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


@api_bp.route('/merchant/services/<int:service_id>', methods=['PUT'])
def merchant_update_service(service_id):
    biz, err = require_merchant()
    if err:
        return err
    db = get_db()
    try:
        row = db.execute(
            'SELECT id FROM services WHERE id=%s AND business_id=%s',
            (service_id, biz['id'])
        ).fetchone()
        if not row:
            return jsonify({'error': '服务不存在'}), 404
        data = request.json or {}
        fields = []
        params = []
        if 'name' in data:
            fields.append('name=%s')
            params.append((data.get('name') or '').strip())
        if 'price' in data:
            price = data.get('price')
            fields.append('price=%s')
            params.append(float(price) if price is not None else None)
        if 'duration' in data:
            fields.append('duration_mins=%s')
            params.append(int(data.get('duration')))
        if 'emoji' in data:
            fields.append('emoji=%s')
            params.append((data.get('emoji') or '').strip())
        if 'description' in data:
            fields.append('name_sub=%s')
            params.append((data.get('description') or '').strip())
        if fields:
            params.extend([service_id, biz['id']])
            db.execute(
                'UPDATE services SET ' + ', '.join(fields) + ' WHERE id=%s AND business_id=%s',
                tuple(params)
            )
            db.commit()
        svc = db.execute(
            'SELECT * FROM services WHERE id=%s AND business_id=%s',
            (service_id, biz['id'])
        ).fetchone()
        return jsonify(dict(svc))
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


@api_bp.route('/merchant/analytics')
def merchant_analytics():
    biz, err = require_merchant()
    if err:
        return err
    db = get_db()
    try:
        now = datetime.now()
        this_month = now.strftime('%Y-%m')
        last_month = (now.replace(day=1) - timedelta(days=1)).strftime('%Y-%m')
        tomorrow = (now + timedelta(days=1)).strftime('%Y-%m-%d')

        rev_row = db.execute(
            "SELECT "
            "SUM(CASE WHEN SUBSTRING(a.appointment_dt, 1, 7) = %s THEN s.price ELSE 0 END) AS rev_this_month, "
            "SUM(CASE WHEN SUBSTRING(a.appointment_dt, 1, 7) = %s THEN s.price ELSE 0 END) AS rev_last_month, "
            "SUM(s.price) AS rev_alltime "
            "FROM appointments a JOIN services s ON a.service_id=s.id "
            "WHERE a.business_id=%s AND a.status='confirmed' AND s.price IS NOT NULL",
            (this_month, last_month, biz['id'])
        ).fetchone()

        cnt_row = db.execute(
            "SELECT "
            "SUM(CASE WHEN SUBSTRING(a.appointment_dt, 1, 7) = %s THEN 1 ELSE 0 END) AS cnt_this_month, "
            "SUM(CASE WHEN SUBSTRING(a.appointment_dt, 1, 7) = %s THEN 1 ELSE 0 END) AS cnt_last_month, "
            "COUNT(*) AS cnt_alltime "
            "FROM appointments a "
            "WHERE a.business_id=%s AND a.status='confirmed'",
            (this_month, last_month, biz['id'])
        ).fetchone()

        top_svcs = db.execute(
            "SELECT s.name, COUNT(*) as count FROM appointments a "
            "JOIN services s ON a.service_id=s.id "
            "WHERE a.business_id=%s AND a.status='confirmed' "
            "GROUP BY s.name ORDER BY count DESC LIMIT 5",
            (biz['id'],)
        ).fetchall()

        tmr_row = db.execute(
            "SELECT COUNT(*) AS cnt FROM appointments "
            "WHERE business_id=%s AND status='confirmed' AND SUBSTRING(appointment_dt, 1, 10) = %s",
            (biz['id'], tomorrow)
        ).fetchone()

        peak_hours = db.execute(
            "SELECT CAST(SUBSTRING(appointment_dt, 12, 2) AS INTEGER) as hour, COUNT(*) as count "
            "FROM appointments WHERE business_id=%s AND status='confirmed' "
            "GROUP BY hour ORDER BY count DESC LIMIT 5",
            (biz['id'],)
        ).fetchall()

        return jsonify({
            'rev_this_month': float(rev_row['rev_this_month'] or 0),
            'rev_last_month': float(rev_row['rev_last_month'] or 0),
            'rev_total': float(rev_row['rev_alltime'] or 0),
            'cnt_this_month': int(cnt_row['cnt_this_month'] or 0),
            'cnt_last_month': int(cnt_row['cnt_last_month'] or 0),
            'cnt_total': int(cnt_row['cnt_alltime'] or 0),
            'tomorrow_count': int(tmr_row['cnt'] or 0),
            'top_services': [{'name': r['name'], 'count': r['count']} for r in top_svcs],
            'peak_hours': [{'hour': r['hour'], 'count': r['count']} for r in peak_hours],
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


@api_bp.route('/wx_login', methods=['POST'])
def wx_login():
    from blueprints.wx import jscode2session, wx_configured
    if not wx_configured():
        return jsonify({'error': 'wx not configured'}), 503
    data = request.json or {}
    code = (data.get('code') or '').strip()
    if not code:
        return jsonify({'error': '缺少 code'}), 400
    openid = jscode2session(code)
    if not openid:
        return jsonify({'error': '微信登录失败'}), 400
    db = get_db()
    try:
        user = db.execute('SELECT * FROM users WHERE openid=%s', (openid,)).fetchone()
        if user:
            token = user['client_token']
        else:
            token = str(uuid.uuid4())
            db.execute(
                'INSERT INTO users (openid, client_token) VALUES (%s,%s)',
                (openid, token)
            )
            db.commit()
        return jsonify({'token': token})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


@api_bp.route('/my/bookings')
def my_bookings():
    user, err = require_client()
    if err:
        return err
    db = get_db()
    try:
        rows = db.execute(
            "SELECT a.cancel_token, a.appointment_dt, a.status, "
            "s.name as service_name, b.name as business_name, b.address "
            "FROM appointments a "
            "JOIN services s ON a.service_id=s.id "
            "JOIN businesses b ON a.business_id=b.id "
            "WHERE a.openid=%s ORDER BY a.appointment_dt DESC",
            (user['openid'],)
        ).fetchall()
        return jsonify({'appointments': [dict(r) for r in rows]})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


@api_bp.route('/categories')
def categories():
    from blueprints.auth import CATEGORIES
    return jsonify({'categories': CATEGORIES})


@api_bp.route('/merchant/register', methods=['POST'])
def merchant_register():
    from blueprints.auth import slugify, CATEGORIES
    data = request.json or {}
    name = (data.get('name') or '').strip()
    email = (data.get('email') or '').strip().lower()
    phone = (data.get('phone') or '').strip()
    password = data.get('password') or ''
    category = (data.get('category') or '').strip()
    slug = slugify(data.get('slug') or name)
    if not all([name, slug, email, phone, password, category]):
        return jsonify({'error': '所有字段为必填项'}), 400
    if len(password) < 6:
        return jsonify({'error': '密码至少 6 个字符'}), 400
    db = get_db()
    try:
        if db.execute('SELECT id FROM businesses WHERE slug=%s', (slug,)).fetchone():
            return jsonify({'error': '该链接地址已被使用'}), 400
        if db.execute('SELECT id FROM businesses WHERE email=%s', (email,)).fetchone():
            return jsonify({'error': '该邮箱已被注册'}), 400
        token = str(uuid.uuid4())
        cur = db.execute(
            'INSERT INTO businesses (name, slug, email, password_hash, phone, category, api_token) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id',
            (name, slug, email, generate_password_hash(password), phone, category, token)
        )
        biz_id = cur.fetchone()['id']
        defaults = [
            (0,'09:00','18:00',0),(1,'09:00','18:00',0),(2,'09:00','18:00',0),
            (3,'09:00','18:00',0),(4,'09:00','18:00',0),(5,'09:00','17:00',0),(6,'09:00','17:00',1),
        ]
        for wd, ot, ct, closed in defaults:
            db.execute(
                'INSERT INTO business_hours (business_id, weekday, open_time, close_time, is_closed) VALUES (%s,%s,%s,%s,%s)',
                (biz_id, wd, ot, ct, closed)
            )
        db.commit()
        return jsonify({'token': token, 'business_name': name, 'id': biz_id})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


ALLOWED_IMG = {'image/jpeg', 'image/png', 'image/webp', 'image/gif'}
MAX_UPLOAD = 5 * 1024 * 1024


def save_upload(file, kind):
    if os.environ.get('CLOUDINARY_CLOUD_NAME'):
        import cloudinary, cloudinary.uploader
        cloudinary.config(
            cloud_name=os.environ.get('CLOUDINARY_CLOUD_NAME', ''),
            api_key=os.environ.get('CLOUDINARY_API_KEY', ''),
            api_secret=os.environ.get('CLOUDINARY_API_SECRET', ''),
        )
        if kind == 'cover':
            folder, trans = 'qi/covers', [{'width': 1200, 'height': 400, 'crop': 'fill'}]
        else:
            folder, trans = 'qi/avatars', [{'width': 400, 'height': 400, 'crop': 'fill'}]
        result = cloudinary.uploader.upload(file, folder=folder, transformation=trans)
        return result.get('secure_url')
    upload_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static', 'uploads')
    os.makedirs(upload_dir, exist_ok=True)
    ext = os.path.splitext(secure_filename(file.filename or ''))[1].lower() or '.jpg'
    fname = f'{kind}_{uuid.uuid4().hex}{ext}'
    file.save(os.path.join(upload_dir, fname))
    return url_for('static', filename=f'uploads/{fname}', _external=True)


@api_bp.route('/merchant/upload', methods=['POST'])
def merchant_upload():
    biz, err = require_merchant()
    if err:
        return err
    file = request.files.get('file')
    if not file or not file.filename:
        return jsonify({'error': '缺少文件'}), 400
    kind = (request.values.get('type') or 'avatar').strip()
    if kind not in ('avatar', 'cover'):
        kind = 'avatar'
    if file.mimetype not in ALLOWED_IMG:
        return jsonify({'error': '仅支持图片格式'}), 400
    file.seek(0, os.SEEK_END)
    if file.tell() > MAX_UPLOAD:
        return jsonify({'error': '图片不能超过 5MB'}), 400
    file.seek(0)
    try:
        url = save_upload(file, kind)
        return jsonify({'url': url})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@api_bp.route('/merchant/profile', methods=['PUT'])
def merchant_update_profile():
    biz, err = require_merchant()
    if err:
        return err
    data = request.json or {}
    fields = []
    params = []
    for key in ('name', 'description', 'address', 'phone', 'avatar_url', 'cover_url'):
        if key in data:
            fields.append(f'{key}=%s')
            params.append((data.get(key) or '').strip())
    if not fields:
        return jsonify({'error': '没有可更新的字段'}), 400
    db = get_db()
    try:
        params.append(biz['id'])
        db.execute('UPDATE businesses SET ' + ', '.join(fields) + ' WHERE id=%s', tuple(params))
        db.commit()
        row = db.execute('SELECT * FROM businesses WHERE id=%s', (biz['id'],)).fetchone()
        row = dict(row)
        row.pop('password_hash', None)
        row.pop('api_token', None)
        return jsonify({'business': row})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


@api_bp.route('/merchant/hours', methods=['GET'])
def merchant_get_hours():
    biz, err = require_merchant()
    if err:
        return err
    db = get_db()
    try:
        rows = db.execute(
            'SELECT * FROM business_hours WHERE business_id=%s ORDER BY weekday',
            (biz['id'],)
        ).fetchall()
        hours_map = {r['weekday']: {'open_time': r['open_time'], 'close_time': r['close_time'], 'is_closed': r['is_closed']} for r in rows}
        days = []
        day_names = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
        for i in range(7):
            d = hours_map.get(i, {'open_time': '09:00', 'close_time': '18:00', 'is_closed': 0})
            days.append({'weekday': i, 'name': day_names[i], **d})
        return jsonify({'hours': days})
    finally:
        db.close()


@api_bp.route('/merchant/hours', methods=['PUT'])
def merchant_update_hours():
    biz, err = require_merchant()
    if err:
        return err
    data = request.json or {}
    days = data.get('days', [])
    db = get_db()
    try:
        for d in days:
            db.execute(
                '''INSERT INTO business_hours (business_id, weekday, open_time, close_time, is_closed)
                   VALUES (%s,%s,%s,%s,%s)
                   ON CONFLICT (business_id, weekday)
                   DO UPDATE SET open_time=EXCLUDED.open_time, close_time=EXCLUDED.close_time, is_closed=EXCLUDED.is_closed''',
                (biz['id'], d['weekday'], d.get('open_time', '09:00'), d.get('close_time', '18:00'), 1 if d.get('is_closed') else 0)
            )
        db.commit()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


@api_bp.route('/merchant/blackouts', methods=['GET'])
def merchant_get_blackouts():
    biz, err = require_merchant()
    if err:
        return err
    db = get_db()
    try:
        rows = db.execute(
            'SELECT * FROM business_blackouts WHERE business_id=%s ORDER BY start_date',
            (biz['id'],)
        ).fetchall()
        return jsonify({'blackouts': [dict(r) for r in rows]})
    finally:
        db.close()


@api_bp.route('/merchant/blackouts', methods=['POST'])
def merchant_add_blackout():
    biz, err = require_merchant()
    if err:
        return err
    data = request.json or {}
    start = (data.get('start_date') or '').strip()
    end = (data.get('end_date') or '').strip()
    reason = (data.get('reason') or '').strip()
    if not start or not end or end < start:
        return jsonify({'error': '日期范围无效'}), 400
    db = get_db()
    try:
        row = db.execute(
            'INSERT INTO business_blackouts (business_id, start_date, end_date, reason) VALUES (%s,%s,%s,%s) RETURNING id',
            (biz['id'], start, end, reason)
        ).fetchone()
        db.commit()
        return jsonify({'id': row['id'], 'start_date': start, 'end_date': end, 'reason': reason})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


@api_bp.route('/merchant/blackouts/<int:bo_id>', methods=['DELETE'])
def merchant_delete_blackout(bo_id):
    biz, err = require_merchant()
    if err:
        return err
    db = get_db()
    try:
        db.execute('DELETE FROM business_blackouts WHERE id=%s AND business_id=%s', (bo_id, biz['id']))
        db.commit()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()

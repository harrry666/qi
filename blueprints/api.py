from flask import Blueprint, jsonify, request, url_for
from db import get_db
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

_LA = ZoneInfo('America/Los_Angeles')
import os
import uuid
import threading
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

api_bp = Blueprint('api', __name__, url_prefix='/api')

TWILIO_VERIFY_SID = os.environ.get('TWILIO_VERIFY_SID', '')


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
    from blueprints.booking import slots_for_service
    date_str = request.args.get('date', '')
    service_id = request.args.get('service_id')
    staff_id = request.args.get('staff_id') or None
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
        available = slots_for_service(biz['id'], date_obj, svc['duration_mins'], service_id, staff_id=staff_id)
        return jsonify({'slots': available})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


@api_bp.route('/businesses/<slug>/staff')
def get_business_staff(slug):
    from blueprints.booking import get_active_staff_for_service
    db = get_db()
    try:
        biz = db.execute('SELECT * FROM businesses WHERE slug=%s', (slug,)).fetchone()
        if not biz:
            return jsonify({'error': 'Not found'}), 404
        biz = dict(biz)
        try:
            service_id = int(request.args.get('service_id', 0))
        except (ValueError, TypeError):
            return jsonify({'staff': []})
        staff = get_active_staff_for_service(biz['id'], service_id)
        return jsonify({'staff': [
            {'id': s['id'], 'name': s['name'], 'emoji': s['emoji'], 'avatar_url': s['avatar_url']}
            for s in staff
        ]})
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
    verify_code = (data.get('verify_code') or '').strip()
    subscribe_authed = 1 if data.get('subscribe_authed') in (1, '1', True) else 0

    if not all([slug, service_id, customer_name, phone, date, time]):
        return jsonify({'error': '缺少必填字段'}), 400

    if TWILIO_VERIFY_SID:
        if not verify_code:
            return jsonify({'error': '请输入手机验证码'}), 400
        try:
            from twilio.rest import Client
            from blueprints.booking import TWILIO_SID, TWILIO_TOKEN, format_phone
            _client = Client(TWILIO_SID, TWILIO_TOKEN)
            check = _client.verify.v2.services(TWILIO_VERIFY_SID).verification_checks.create(
                to=format_phone(phone), code=verify_code
            )
            if check.status != 'approved':
                return jsonify({'error': '验证码错误或已过期'}), 400
        except Exception:
            return jsonify({'error': '验证失败，请重新获取验证码'}), 400

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
            if apt_dt_obj < datetime.now(_LA).replace(tzinfo=None):
                return jsonify({'error': '不能预约过去的时间'}), 400
        except ValueError:
            return jsonify({'error': '日期时间格式无效'}), 400

        from blueprints.booking import resolve_staff_id
        staff_id = resolve_staff_id(biz['id'], service_id, date, time, svc['duration_mins'], data.get('staff_id') or None)

        cancel_token = str(uuid.uuid4())
        db.execute(
            'INSERT INTO appointments (business_id, service_id, customer_name, phone, appointment_dt, comment, status, cancel_token, openid, subscribe_authed, staff_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
            (biz['id'], service_id, customer_name, phone, appointment_dt, comment, 'confirmed', cancel_token, openid, subscribe_authed, staff_id)
        )
        db.commit()

        try:
            dt_display = apt_dt_obj.strftime('%Y年%-m月%-d日 %-H:%M')
        except Exception:
            dt_display = appointment_dt

        biz_phone = biz.get('phone') or ''
        formatted_customer_phone = format_phone(phone)
        _base = os.environ.get('BASE_URL', '').rstrip('/')
        cancel_url = f"{_base}/cancel/{cancel_token}" if _base else ''

        customer_msg = (
            f"【预约确认】{customer_name}，您在【{biz['name']}】的预约已确认。\n\n"
            f"服务：{svc['name']}\n"
            f"时间：{dt_display}\n"
            + (f"地址：{biz['address']}\n" if biz.get('address') else '')
            + (f"如有疑问请致电：{biz_phone}\n" if biz_phone else '')
            + (f"\n如需取消：{cancel_url}" if cancel_url else '')
            + "\n或直接回复本短信「取消」"
        )
        threading.Thread(target=send_sms, args=(formatted_customer_phone, customer_msg), daemon=True).start()

        if biz_phone:
            import re as _re
            last4 = _re.sub(r'[^0-9]', '', phone)[-4:]
            owner_msg = (
                f"【新预约】{biz['name']}\n\n"
                f"客人：{customer_name}\n"
                f"电话：{phone}\n"
                f"服务：{svc['name']}\n"
                f"时间：{dt_display}\n"
                + (f"备注：{comment}\n" if comment else '')
                + f"\n如需取消，回复「取消 {last4}」"
            )
            threading.Thread(target=send_sms, args=(format_phone(biz_phone), owner_msg), daemon=True).start()

        return jsonify({'success': True, 'cancel_token': cancel_token, 'message': '预约成功'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


@api_bp.route('/verify/send', methods=['POST'])
def verify_send():
    from blueprints.booking import TWILIO_SID, TWILIO_TOKEN, format_phone
    data = request.json or {}
    phone = (data.get('phone') or '').strip()
    if not phone:
        return jsonify({'error': '请输入手机号'}), 400
    if not TWILIO_VERIFY_SID:
        return jsonify({'error': '验证服务未配置'}), 500
    try:
        from twilio.rest import Client
        client = Client(TWILIO_SID, TWILIO_TOKEN)
        client.verify.v2.services(TWILIO_VERIFY_SID).verifications.create(
            to=format_phone(phone), channel='sms', locale='zh'
        )
        return jsonify({'sent': True})
    except Exception as e:
        return jsonify({'error': '发送失败，请检查手机号格式'}), 400


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
            owner_msg = (
                f"【预约取消】{row['biz_name']}\n\n"
                f"客人：{row['customer_name']}\n"
                f"服务：{row['service_name']}\n"
                f"原定时间：{dt_display}"
            )
            threading.Thread(target=send_sms, args=(format_phone(row['biz_phone']), owner_msg), daemon=True).start()

        if row.get('phone'):
            customer_msg = (
                f"【取消确认】{row['customer_name']}，您在【{row['biz_name']}】的预约已成功取消。\n\n"
                f"服务：{row['service_name']}\n"
                f"原定时间：{dt_display}"
            )
            threading.Thread(target=send_sms, args=(format_phone(row['phone']), customer_msg), daemon=True).start()

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
        if not biz.get('is_approved'):
            return jsonify({'error': '账号待审核，请联系管理员'}), 403
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
            "SELECT a.*, s.name as service_name, s.duration_mins, s.price, st.name as staff_name "
            "FROM appointments a JOIN services s ON a.service_id=s.id "
            "LEFT JOIN staff st ON a.staff_id=st.id "
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


def _parse_duration_range(data):
    """返回 (blocking_mins, display_min)。最长用于排档，最短仅用于显示。"""
    try:
        short = int(data.get('duration', data.get('duration_mins', 30)) or 30)
    except (ValueError, TypeError):
        short = 30
    long_raw = data.get('duration_max')
    try:
        long_v = int(long_raw) if long_raw not in (None, '') else None
    except (ValueError, TypeError):
        long_v = None
    if long_v and long_v > short:
        return long_v, short
    return short, None


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
    duration_mins, duration_min_mins = _parse_duration_range(data)
    price = data.get('price')
    if price is not None:
        price = float(price)
    emoji = (data.get('emoji') or '').strip()
    try:
        buffer_mins = int(data.get('buffer_mins') or 0)
    except (ValueError, TypeError):
        buffer_mins = 0
    color = (data.get('color') or '').strip()
    db = get_db()
    try:
        cur = db.execute(
            'INSERT INTO services (business_id, name, name_sub, duration_mins, duration_min_mins, price, emoji, buffer_mins, color, is_active, sort_order) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,1,0) RETURNING id',
            (biz['id'], name, name_sub, duration_mins, duration_min_mins, price, emoji, buffer_mins, color)
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
        if 'duration' in data or 'duration_mins' in data:
            dur_mins, dur_min = _parse_duration_range(data)
            fields.append('duration_mins=%s')
            params.append(dur_mins)
            fields.append('duration_min_mins=%s')
            params.append(dur_min)
        if 'emoji' in data:
            fields.append('emoji=%s')
            params.append((data.get('emoji') or '').strip())
        if 'buffer_mins' in data:
            try:
                bm = int(data.get('buffer_mins') or 0)
            except (ValueError, TypeError):
                bm = 0
            fields.append('buffer_mins=%s')
            params.append(bm)
        if 'name_sub' in data or 'description' in data:
            fields.append('name_sub=%s')
            params.append((data.get('name_sub') or data.get('description') or '').strip())
        if 'color' in data:
            fields.append('color=%s')
            params.append((data.get('color') or '').strip())
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


@api_bp.route('/my/profile')
def my_profile():
    user, err = require_client()
    if err:
        return err
    return jsonify({
        'nickname': user.get('nickname') or '',
        'avatar_url': user.get('avatar_url') or '',
        'phone': user.get('phone') or '',
        'preferences': user.get('preferences') or ''
    })


@api_bp.route('/my/profile', methods=['PUT'])
def my_update_profile():
    user, err = require_client()
    if err:
        return err
    data = request.json or {}
    fields = []
    params = []
    if 'nickname' in data:
        fields.append('nickname=%s')
        params.append((data.get('nickname') or '').strip())
    if 'phone' in data:
        fields.append('phone=%s')
        params.append((data.get('phone') or '').strip())
    if 'preferences' in data:
        fields.append('preferences=%s')
        params.append((data.get('preferences') or '').strip())
    if not fields:
        return jsonify({'ok': True})
    db = get_db()
    try:
        params.append(user['id'])
        db.execute('UPDATE users SET ' + ', '.join(fields) + ' WHERE id=%s', tuple(params))
        db.commit()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


@api_bp.route('/my/avatar', methods=['POST'])
def my_upload_avatar():
    user, err = require_client()
    if err:
        return err
    file = request.files.get('file')
    if not file or not file.filename:
        return jsonify({'error': '缺少文件'}), 400
    if file.mimetype not in ALLOWED_IMG:
        return jsonify({'error': '仅支持图片格式'}), 400
    file.seek(0, os.SEEK_END)
    if file.tell() > MAX_UPLOAD:
        return jsonify({'error': '图片不能超过 5MB'}), 400
    file.seek(0)
    db = get_db()
    try:
        url = save_upload(file, 'avatar')
        db.execute('UPDATE users SET avatar_url=%s WHERE id=%s', (url, user['id']))
        db.commit()
        return jsonify({'url': url})
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
            'INSERT INTO businesses (name, slug, email, password_hash, phone, category, api_token, is_approved) VALUES (%s,%s,%s,%s,%s,%s,%s,0) RETURNING id',
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
        return jsonify({'pending': True, 'message': '注册申请已提交，管理员审核通过后即可登录'})
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
        elif kind == 'photo':
            folder, trans = 'qi/customer_photos', [{'width': 1200, 'height': 1200, 'crop': 'limit'}]
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
    if kind not in ('avatar', 'cover', 'photo'):
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


@api_bp.route('/merchant/blocks', methods=['GET'])
def merchant_get_blocks():
    biz, err = require_merchant()
    if err:
        return err
    db = get_db()
    try:
        rows = db.execute(
            'SELECT tb.*, st.name AS staff_name FROM time_blocks tb '
            'LEFT JOIN staff st ON tb.staff_id=st.id '
            'WHERE tb.business_id=%s ORDER BY tb.date, tb.start_time',
            (biz['id'],)
        ).fetchall()
        return jsonify({'blocks': [dict(r) for r in rows]})
    finally:
        db.close()


@api_bp.route('/merchant/blocks', methods=['POST'])
def merchant_add_block():
    biz, err = require_merchant()
    if err:
        return err
    data = request.json or {}
    start_date = (data.get('start_date') or '').strip()
    end_date = (data.get('end_date') or '').strip() or start_date
    start_time = (data.get('start_time') or '').strip()
    end_time = (data.get('end_time') or '').strip()
    reason = (data.get('reason') or '').strip()
    staff_id = data.get('staff_id') or None
    if not start_date or not start_time or not end_time or end_time <= start_time or end_date < start_date:
        return jsonify({'error': '日期或时间范围无效'}), 400
    db = get_db()
    try:
        if staff_id:
            own = db.execute('SELECT id FROM staff WHERE id=%s AND business_id=%s', (staff_id, biz['id'])).fetchone()
            if not own:
                staff_id = None
        d0 = datetime.strptime(start_date, '%Y-%m-%d').date()
        d1 = datetime.strptime(end_date, '%Y-%m-%d').date()
        if (d1 - d0).days > 60:
            return jsonify({'error': '日期范围不能超过 60 天'}), 400
        cur = d0
        while cur <= d1:
            db.execute(
                'INSERT INTO time_blocks (business_id, staff_id, date, start_time, end_time, reason) VALUES (%s,%s,%s,%s,%s,%s)',
                (biz['id'], staff_id, cur.strftime('%Y-%m-%d'), start_time, end_time, reason)
            )
            cur += timedelta(days=1)
        db.commit()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


@api_bp.route('/merchant/blocks/<int:bid>', methods=['DELETE'])
def merchant_delete_block(bid):
    biz, err = require_merchant()
    if err:
        return err
    db = get_db()
    try:
        db.execute('DELETE FROM time_blocks WHERE id=%s AND business_id=%s', (bid, biz['id']))
        db.commit()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


@api_bp.route('/merchant/staff', methods=['GET'])
def merchant_staff():
    biz, err = require_merchant()
    if err:
        return err
    db = get_db()
    try:
        rows = db.execute(
            'SELECT * FROM staff WHERE business_id=%s ORDER BY sort_order, id',
            (biz['id'],)
        ).fetchall()
        result = []
        for r in rows:
            s = dict(r)
            svc_rows = db.execute('SELECT service_id FROM staff_services WHERE staff_id=%s', (s['id'],)).fetchall()
            hour_rows = db.execute('SELECT weekday, open_time, close_time, is_closed FROM staff_hours WHERE staff_id=%s ORDER BY weekday', (s['id'],)).fetchall()
            result.append({
                'id': s['id'], 'name': s['name'], 'emoji': s['emoji'], 'avatar_url': s['avatar_url'],
                'bio': s['bio'], 'is_active': s['is_active'],
                'service_ids': [sr['service_id'] for sr in svc_rows],
                'hours': [dict(h) for h in hour_rows],
            })
        return jsonify({'staff': result})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


@api_bp.route('/merchant/staff', methods=['POST'])
def merchant_add_staff():
    biz, err = require_merchant()
    if err:
        return err
    data = request.json or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'error': '员工姓名不能为空'}), 400
    emoji = (data.get('emoji') or '').strip()
    bio = (data.get('bio') or '').strip()
    avatar_url = (data.get('avatar_url') or '').strip()
    db = get_db()
    try:
        row = db.execute(
            'INSERT INTO staff (business_id, name, emoji, bio, avatar_url) VALUES (%s,%s,%s,%s,%s) RETURNING id',
            (biz['id'], name, emoji, bio, avatar_url)
        ).fetchone()
        db.commit()
        return jsonify({'id': row['id']})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


@api_bp.route('/merchant/staff/<int:sid>', methods=['PUT'])
def merchant_update_staff(sid):
    biz, err = require_merchant()
    if err:
        return err
    db = get_db()
    try:
        row = db.execute('SELECT id FROM staff WHERE id=%s AND business_id=%s', (sid, biz['id'])).fetchone()
        if not row:
            return jsonify({'error': '员工不存在'}), 404
        data = request.json or {}
        fields = []
        params = []
        if 'name' in data:
            fields.append('name=%s')
            params.append((data.get('name') or '').strip())
        if 'emoji' in data:
            fields.append('emoji=%s')
            params.append((data.get('emoji') or '').strip())
        if 'bio' in data:
            fields.append('bio=%s')
            params.append((data.get('bio') or '').strip())
        if 'avatar_url' in data:
            fields.append('avatar_url=%s')
            params.append((data.get('avatar_url') or '').strip())
        if 'is_active' in data:
            fields.append('is_active=%s')
            params.append(1 if data.get('is_active') in (1, '1', True) else 0)
        if fields:
            params.extend([sid, biz['id']])
            db.execute('UPDATE staff SET ' + ', '.join(fields) + ' WHERE id=%s AND business_id=%s', tuple(params))
            db.commit()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


@api_bp.route('/merchant/staff/<int:sid>', methods=['DELETE'])
def merchant_delete_staff(sid):
    biz, err = require_merchant()
    if err:
        return err
    db = get_db()
    try:
        row = db.execute('SELECT id FROM staff WHERE id=%s AND business_id=%s', (sid, biz['id'])).fetchone()
        if not row:
            return jsonify({'error': '员工不存在'}), 404
        db.execute('DELETE FROM staff_hours WHERE staff_id=%s', (sid,))
        db.execute('DELETE FROM staff_services WHERE staff_id=%s', (sid,))
        db.execute('DELETE FROM staff WHERE id=%s AND business_id=%s', (sid, biz['id']))
        db.commit()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


@api_bp.route('/merchant/staff/<int:sid>/services', methods=['PUT'])
def merchant_staff_services(sid):
    biz, err = require_merchant()
    if err:
        return err
    db = get_db()
    try:
        row = db.execute('SELECT id FROM staff WHERE id=%s AND business_id=%s', (sid, biz['id'])).fetchone()
        if not row:
            return jsonify({'error': '员工不存在'}), 404
        data = request.json or {}
        service_ids = data.get('service_ids') or []
        db.execute('DELETE FROM staff_services WHERE staff_id=%s', (sid,))
        for svc_id in service_ids:
            valid = db.execute('SELECT id FROM services WHERE id=%s AND business_id=%s', (svc_id, biz['id'])).fetchone()
            if valid:
                db.execute('INSERT INTO staff_services (staff_id, service_id) VALUES (%s,%s) ON CONFLICT (staff_id, service_id) DO NOTHING', (sid, svc_id))
        db.commit()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


@api_bp.route('/merchant/staff/<int:sid>/hours', methods=['PUT'])
def merchant_staff_hours(sid):
    biz, err = require_merchant()
    if err:
        return err
    db = get_db()
    try:
        row = db.execute('SELECT id FROM staff WHERE id=%s AND business_id=%s', (sid, biz['id'])).fetchone()
        if not row:
            return jsonify({'error': '员工不存在'}), 404
        data = request.json or {}
        for h in data.get('hours', []):
            db.execute(
                '''INSERT INTO staff_hours (staff_id, weekday, open_time, close_time, is_closed)
                   VALUES (%s,%s,%s,%s,%s)
                   ON CONFLICT (staff_id, weekday)
                   DO UPDATE SET open_time=EXCLUDED.open_time, close_time=EXCLUDED.close_time, is_closed=EXCLUDED.is_closed''',
                (sid, h['weekday'], h.get('open_time', '09:00'), h.get('close_time', '18:00'), 1 if h.get('is_closed') else 0)
            )
        db.commit()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


@api_bp.route('/merchant/customers', methods=['GET'])
def merchant_customers():
    biz, err = require_merchant()
    if err:
        return err
    db = get_db()
    try:
        rows = db.execute(
            "SELECT c.id, c.name, c.phone, c.avatar_url, c.balance, "
            "COUNT(a.id) as visit_count, MAX(a.appointment_dt) as last_visit "
            "FROM customers c "
            "LEFT JOIN appointments a ON a.customer_id = c.id AND a.status='confirmed' "
            "WHERE c.business_id=%s "
            "GROUP BY c.id "
            "ORDER BY visit_count DESC, last_visit DESC NULLS LAST",
            (biz['id'],)
        ).fetchall()
        return jsonify({'customers': [dict(r) for r in rows]})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


@api_bp.route('/merchant/customers', methods=['POST'])
def merchant_add_customer():
    from db import upsert_customer
    biz, err = require_merchant()
    if err:
        return err
    data = request.json or {}
    name = (data.get('name') or '').strip()
    phone = (data.get('phone') or '').strip()
    preferences = (data.get('preferences') or '').strip()
    private_note = (data.get('private_note') or '').strip()
    balance = data.get('balance')
    if not name or not phone:
        return jsonify({'error': '姓名和手机号必填'}), 400
    db = get_db()
    try:
        existing = db.execute('SELECT id FROM customers WHERE business_id=%s AND phone=%s', (biz['id'], phone)).fetchone()
        if existing:
            return jsonify({'error': '该手机号已有客户档案'}), 400
        cid = upsert_customer(db, biz['id'], phone, name)
        try:
            balance_val = int(balance) if balance not in (None, '') else 0
        except (ValueError, TypeError):
            balance_val = 0
        db.execute(
            'UPDATE customers SET preferences=%s, private_note=%s, balance=%s WHERE id=%s',
            (preferences, private_note, balance_val, cid)
        )
        if balance_val:
            db.execute('INSERT INTO balance_transactions (customer_id, delta, reason) VALUES (%s,%s,%s)',
                       (cid, balance_val, '建档初始余额'))
        db.commit()
        return jsonify({'success': True, 'customer_id': cid})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


@api_bp.route('/merchant/customers/<int:cid>', methods=['GET'])
def merchant_customer_detail(cid):
    biz, err = require_merchant()
    if err:
        return err
    db = get_db()
    try:
        cust = db.execute('SELECT * FROM customers WHERE id=%s AND business_id=%s', (cid, biz['id'])).fetchone()
        if not cust:
            return jsonify({'error': '未找到该客户'}), 404
        cust = dict(cust)
        visits = db.execute(
            "SELECT a.appointment_dt, s.name as service_name, a.status, a.comment "
            "FROM appointments a JOIN services s ON a.service_id=s.id "
            "WHERE a.customer_id=%s ORDER BY a.appointment_dt DESC LIMIT 20",
            (cid,)
        ).fetchall()
        photos = db.execute(
            "SELECT id, photo_url, note FROM customer_photos WHERE customer_id=%s ORDER BY created_at DESC",
            (cid,)
        ).fetchall()
        transactions = db.execute(
            "SELECT delta, reason, created_at FROM balance_transactions WHERE customer_id=%s ORDER BY created_at DESC LIMIT 20",
            (cid,)
        ).fetchall()
        return jsonify({
            'customer': {
                'id': cust['id'], 'name': cust['name'], 'phone': cust['phone'],
                'avatar_url': cust.get('avatar_url'), 'balance': cust.get('balance'),
                'preferences': cust.get('preferences'), 'private_note': cust.get('private_note'),
            },
            'visits': [dict(v) for v in visits],
            'photos': [dict(p) for p in photos],
            'transactions': [dict(t) for t in transactions],
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


@api_bp.route('/merchant/customers/<int:cid>', methods=['PUT'])
def merchant_customer_update(cid):
    biz, err = require_merchant()
    if err:
        return err
    data = request.json or {}
    name = (data.get('name') or '').strip()
    phone = (data.get('phone') or '').strip()
    preferences = (data.get('preferences') or '').strip()
    private_note = (data.get('private_note') or '').strip()
    db = get_db()
    try:
        own = db.execute('SELECT id FROM customers WHERE id=%s AND business_id=%s', (cid, biz['id'])).fetchone()
        if not own:
            return jsonify({'error': '未找到该客户'}), 404
        if phone:
            clash = db.execute(
                'SELECT id FROM customers WHERE business_id=%s AND phone=%s AND id!=%s',
                (biz['id'], phone, cid)
            ).fetchone()
            if clash:
                return jsonify({'error': '该手机号已被其他客户占用'}), 400
        db.execute(
            'UPDATE customers SET name=%s, phone=%s, preferences=%s, private_note=%s WHERE id=%s AND business_id=%s',
            (name, phone, preferences, private_note, cid, biz['id'])
        )
        db.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


@api_bp.route('/merchant/customers/<int:cid>/balance', methods=['POST'])
def merchant_customer_adjust_balance(cid):
    biz, err = require_merchant()
    if err:
        return err
    data = request.json or {}
    try:
        delta = int(data.get('delta', 0))
    except (ValueError, TypeError):
        delta = 0
    reason = (data.get('reason') or '').strip()
    db = get_db()
    try:
        cust = db.execute('SELECT id, balance FROM customers WHERE id=%s AND business_id=%s', (cid, biz['id'])).fetchone()
        if not cust:
            return jsonify({'error': '未找到该客户'}), 404
        if delta:
            db.execute('UPDATE customers SET balance = balance + %s WHERE id=%s', (delta, cid))
            db.execute(
                'INSERT INTO balance_transactions (customer_id, delta, reason) VALUES (%s,%s,%s)',
                (cid, delta, reason)
            )
            db.commit()
        row = db.execute('SELECT balance FROM customers WHERE id=%s', (cid,)).fetchone()
        return jsonify({'success': True, 'balance': row['balance']})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


@api_bp.route('/merchant/customers/<int:cid>/photo', methods=['POST'])
def merchant_customer_add_photo(cid):
    biz, err = require_merchant()
    if err:
        return err
    data = request.json or {}
    photo_url = (data.get('photo_url') or '').strip()
    note = (data.get('note') or '').strip()
    if not photo_url:
        return jsonify({'error': '缺少图片'}), 400
    db = get_db()
    try:
        cust = db.execute('SELECT id FROM customers WHERE id=%s AND business_id=%s', (cid, biz['id'])).fetchone()
        if not cust:
            return jsonify({'error': '未找到该客户'}), 404
        db.execute(
            "INSERT INTO customer_photos (customer_id, photo_url, note, uploaded_by) VALUES (%s,%s,%s,'merchant')",
            (cid, photo_url, note)
        )
        db.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


@api_bp.route('/merchant/customers/<int:cid>', methods=['DELETE'])
def merchant_customer_delete(cid):
    biz, err = require_merchant()
    if err:
        return err
    db = get_db()
    try:
        own = db.execute('SELECT id FROM customers WHERE id=%s AND business_id=%s', (cid, biz['id'])).fetchone()
        if not own:
            return jsonify({'error': '未找到该客户'}), 404
        db.execute('DELETE FROM customer_photos WHERE customer_id=%s', (cid,))
        db.execute('DELETE FROM balance_transactions WHERE customer_id=%s', (cid,))
        db.execute('UPDATE appointments SET customer_id=NULL WHERE customer_id=%s AND business_id=%s', (cid, biz['id']))
        db.execute('DELETE FROM customers WHERE id=%s AND business_id=%s', (cid, biz['id']))
        db.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


@api_bp.route('/merchant/feedback', methods=['POST'])
def merchant_feedback():
    biz, err = require_merchant()
    if err:
        return err
    data = request.json or {}
    message = (data.get('message') or '').strip()
    if not message:
        return jsonify({'error': '反馈内容不能为空'}), 400
    db = get_db()
    try:
        db.execute(
            "INSERT INTO platform_feedback (source, business_id, name, contact, message) VALUES ('merchant',%s,%s,%s,%s)",
            (biz['id'], biz['name'], biz.get('email'), message)
        )
        db.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


@api_bp.route('/merchant/appointments', methods=['POST'])
def merchant_create_appointment():
    from db import upsert_customer
    from blueprints.booking import send_sms, format_phone
    biz, err = require_merchant()
    if err:
        return err
    data = request.json or {}
    service_id = (str(data.get('service_id') or '')).strip()
    staff_id = data.get('staff_id') or None
    name = (data.get('customer_name') or '').strip()
    phone = (data.get('phone') or '').strip()
    date = (data.get('date') or '').strip()
    time_ = (data.get('time') or '').strip()
    comment = (data.get('comment') or '').strip()
    if not all([service_id, name, phone, date, time_]):
        return jsonify({'error': '请填写完整信息'}), 400
    db = get_db()
    try:
        svc = db.execute('SELECT id, name FROM services WHERE id=%s AND business_id=%s', (service_id, biz['id'])).fetchone()
        if not svc:
            return jsonify({'error': '服务不存在'}), 404
        svc = dict(svc)
        if staff_id:
            own = db.execute('SELECT id FROM staff WHERE id=%s AND business_id=%s', (staff_id, biz['id'])).fetchone()
            if not own:
                staff_id = None
        apt_dt = f'{date} {time_}'
        customer_id = upsert_customer(db, biz['id'], phone, name)
        cancel_token = str(uuid.uuid4())
        db.execute(
            'INSERT INTO appointments (business_id, service_id, customer_name, phone, appointment_dt, comment, cancel_token, staff_id, customer_id) '
            'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)',
            (biz['id'], service_id, name, phone, apt_dt, comment, cancel_token, staff_id, customer_id)
        )
        db.commit()
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()

    try:
        dt_display = datetime.strptime(apt_dt, '%Y-%m-%d %H:%M').strftime('%Y年%-m月%-d日 %-H:%M')
    except ValueError:
        dt_display = apt_dt
    _base = os.environ.get('BASE_URL', '').rstrip('/')
    cancel_url = f"{_base}/cancel/{cancel_token}" if _base else ''
    biz_phone = biz.get('phone') or ''
    customer_msg = (
        f"【预约确认】{name}，您在【{biz['name']}】的预约已确认。\n\n"
        f"服务：{svc['name']}\n"
        f"时间：{dt_display}\n"
        + (f"如有疑问请致电：{biz_phone}\n" if biz_phone else '')
        + (f"\n如需取消：{cancel_url}" if cancel_url else '')
        + "\n或直接回复本短信「取消」"
    )
    threading.Thread(target=send_sms, args=(format_phone(phone), customer_msg), daemon=True).start()
    return jsonify({'success': True})


@api_bp.route('/merchant/appointments/<int:apt_id>/reschedule', methods=['POST'])
def merchant_reschedule_appointment(apt_id):
    biz, err = require_merchant()
    if err:
        return err
    data = request.json or {}
    new_dt_raw = (data.get('new_dt') or '').strip()
    if not new_dt_raw:
        return jsonify({'error': '请选择新的日期和时间'}), 400
    try:
        new_dt = datetime.strptime(new_dt_raw, '%Y-%m-%dT%H:%M')
    except ValueError:
        return jsonify({'error': '日期格式无效'}), 400
    new_dt_str = new_dt.strftime('%Y-%m-%d %H:%M')
    db = get_db()
    try:
        own = db.execute('SELECT id FROM appointments WHERE id=%s AND business_id=%s', (apt_id, biz['id'])).fetchone()
        if not own:
            return jsonify({'error': '预约不存在'}), 404
        db.execute(
            'UPDATE appointments SET appointment_dt=%s WHERE id=%s AND business_id=%s',
            (new_dt_str, apt_id, biz['id'])
        )
        db.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()

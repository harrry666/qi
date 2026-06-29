from flask import Flask
from flask_login import LoginManager
from dotenv import load_dotenv
from extensions import csrf, limiter
import os
import sys
import threading
import atexit
from datetime import datetime, timedelta

load_dotenv()

app = Flask(__name__)
_secret = os.environ.get('SECRET_KEY')
if not _secret:
    raise RuntimeError('SECRET_KEY environment variable is not set')
app.secret_key = _secret
app.config['WTF_CSRF_ENABLED'] = False

csrf.init_app(app)
limiter.init_app(app)

login_manager = LoginManager(app)
login_manager.login_view = 'auth.login'
login_manager.login_message = '请登录后继续。'
login_manager.login_message_category = 'error'

@login_manager.user_loader
def load_user(user_id):
    from models import Business
    from db import get_db
    db = get_db()
    row = db.execute('SELECT * FROM businesses WHERE id=%s', (user_id,)).fetchone()
    db.close()
    return Business(row) if row else None

from blueprints.auth import auth_bp
from blueprints.dashboard import dashboard_bp
from blueprints.booking import booking_bp
from blueprints.api import api_bp
from blueprints.admin import admin_bp

app.register_blueprint(auth_bp)
app.register_blueprint(dashboard_bp)
app.register_blueprint(booking_bp)
app.register_blueprint(api_bp)
app.register_blueprint(admin_bp)
csrf.exempt(admin_bp)
csrf.exempt(booking_bp)

from flask import send_from_directory

@app.route('/robots.txt')
def robots():
    return send_from_directory(app.static_folder, 'robots.txt')

@app.after_request
def set_security_headers(response):
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Permissions-Policy'] = 'geolocation=(), camera=(), microphone=()'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    if os.environ.get('RAILWAY_ENVIRONMENT'):
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdnjs.cloudflare.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data: https: blob:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "form-action 'self'; "
        "base-uri 'self'"
    )
    return response

from db import init_db
init_db()

@app.errorhandler(404)
def not_found(e):
    from flask import render_template
    return render_template('404.html'), 404

@app.errorhandler(500)
def server_error(e):
    from flask import render_template
    return render_template('500.html'), 500


def send_reminders():
    from db import get_db
    from blueprints.booking import send_sms, format_phone
    base_url = os.environ.get('BASE_URL', '').rstrip('/')
    try:
        now = datetime.now()
        window_start = (now + timedelta(hours=23)).strftime('%Y-%m-%d %H:%M')
        window_end   = (now + timedelta(hours=25)).strftime('%Y-%m-%d %H:%M')
        db = get_db()
        rows = db.execute(
            "SELECT a.id, a.customer_name, a.phone, a.appointment_dt, a.cancel_token, "
            "s.name as service_name, b.name as biz_name, b.address "
            "FROM appointments a "
            "JOIN services s ON a.service_id = s.id "
            "JOIN businesses b ON a.business_id = b.id "
            "WHERE a.status = 'confirmed' AND a.reminder_sent = 0 "
            "AND a.appointment_dt >= %s AND a.appointment_dt <= %s",
            (window_start, window_end)
        ).fetchall()
        for row in rows:
            claimed = db.execute(
                "UPDATE appointments SET reminder_sent = 1 WHERE id = %s AND reminder_sent = 0 RETURNING id",
                (row['id'],)
            ).fetchone()
            db.commit()
            if claimed:
                try:
                    dt = datetime.strptime(row['appointment_dt'], '%Y-%m-%d %H:%M')
                    dt_display = dt.strftime('%Y年%-m月%-d日 %-H:%M')
                except Exception:
                    dt_display = row['appointment_dt']
                cancel_part = f"\n\n如需取消：{base_url}/cancel/{row['cancel_token']}" if (base_url and row['cancel_token']) else ''
                msg = (
                    f"【预约提醒】{row['customer_name']}，您明天在【{row['biz_name']}】有一个预约。\n\n"
                    f"服务：{row['service_name']}\n"
                    f"时间：{dt_display}\n"
                    + (f"地址：{row['address']}" if row['address'] else '')
                    + cancel_part
                )
                threading.Thread(target=send_sms, args=(format_phone(row['phone']), msg), daemon=True).start()
        db.close()
    except Exception as e:
        print(f'[Reminder] ERROR: {e}', flush=True, file=sys.stderr)


def send_wx_reminders():
    from db import get_db
    from blueprints.wx import send_subscribe_message, wx_configured
    if not wx_configured():
        return
    try:
        now = datetime.now()
        window_start = (now + timedelta(minutes=30)).strftime('%Y-%m-%d %H:%M')
        window_end   = (now + timedelta(minutes=90)).strftime('%Y-%m-%d %H:%M')
        db = get_db()
        rows = db.execute(
            "SELECT a.id, a.openid, a.appointment_dt, "
            "s.name as service_name, b.name as biz_name, b.address "
            "FROM appointments a "
            "JOIN services s ON a.service_id = s.id "
            "JOIN businesses b ON a.business_id = b.id "
            "WHERE a.status = 'confirmed' AND a.subscribe_authed = 1 "
            "AND a.wx_reminder_sent = 0 AND a.openid IS NOT NULL "
            "AND a.appointment_dt >= %s AND a.appointment_dt <= %s",
            (window_start, window_end)
        ).fetchall()
        for row in rows:
            claimed = db.execute(
                "UPDATE appointments SET wx_reminder_sent = 1 WHERE id = %s AND wx_reminder_sent = 0 RETURNING id",
                (row['id'],)
            ).fetchone()
            db.commit()
            if claimed:
                try:
                    dt = datetime.strptime(row['appointment_dt'], '%Y-%m-%d %H:%M')
                    dt_display = dt.strftime('%Y年%-m月%-d日 %-H:%M')
                except Exception:
                    dt_display = row['appointment_dt']
                data = {
                    'thing1': {'value': row['biz_name'][:20]},
                    'thing2': {'value': row['service_name'][:20]},
                    'time3': {'value': dt_display},
                    'thing4': {'value': (row['address'] or '')[:20]},
                }
                threading.Thread(
                    target=send_subscribe_message, args=(row['openid'], data), daemon=True
                ).start()
        db.close()
    except Exception as e:
        print(f'[WXReminder] ERROR: {e}', flush=True, file=sys.stderr)


try:
    from apscheduler.schedulers.background import BackgroundScheduler
    _scheduler = BackgroundScheduler()
    _scheduler.add_job(send_reminders, 'interval', minutes=15)
    _scheduler.add_job(send_wx_reminders, 'interval', minutes=15)
    _scheduler.start()
    atexit.register(lambda: _scheduler.shutdown(wait=False))
except Exception as _e:
    print(f'[Scheduler] failed to start: {_e}', flush=True, file=sys.stderr)


if __name__ == '__main__':
    app.run(debug=True, port=5002)

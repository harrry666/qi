from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from extensions import limiter
from db import get_db
from models import Business
import re
import secrets
import smtplib
import socket
import ssl
import os
import threading
from email.mime.text import MIMEText
from datetime import datetime, timedelta, timezone

auth_bp = Blueprint('auth', __name__)

MAIL_SERVER   = os.environ.get('MAIL_SERVER', 'smtp.gmail.com')
MAIL_PORT     = int(os.environ.get('MAIL_PORT', 587))
MAIL_USERNAME = os.environ.get('MAIL_USERNAME', '')
MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD', '')
MAIL_FROM     = os.environ.get('MAIL_FROM', MAIL_USERNAME)

def send_email(to, subject, body):
    if not all([MAIL_USERNAME, MAIL_PASSWORD]):
        print(f'[send_email] SKIP: MAIL_USERNAME or MAIL_PASSWORD not set (user set={bool(MAIL_USERNAME)}, pass set={bool(MAIL_PASSWORD)})', flush=True)
        return
    msg = MIMEText(body, 'plain', 'utf-8')
    msg['Subject'] = subject
    msg['From'] = MAIL_FROM
    msg['To'] = to
    try:
        ipv4 = socket.getaddrinfo(MAIL_SERVER, MAIL_PORT, socket.AF_INET, socket.SOCK_STREAM)[0][4][0]
        smtp = smtplib.SMTP(timeout=15)
        smtp.connect(ipv4, MAIL_PORT)
        smtp._host = MAIL_SERVER
        smtp.ehlo()
        smtp.starttls(context=ssl.create_default_context())
        smtp.ehlo()
        smtp.login(MAIL_USERNAME, MAIL_PASSWORD)
        smtp.sendmail(MAIL_FROM, to, msg.as_string())
        smtp.quit()
        print(f'[send_email] OK: sent to {to} via {MAIL_SERVER}({ipv4}):{MAIL_PORT} from {MAIL_FROM}', flush=True)
    except Exception as e:
        print(f'[send_email] FAIL: {type(e).__name__}: {e}', flush=True)

CATEGORIES = [
    'Hair', 'Nails', 'Massage', 'Fitness & Yoga', 'Medical',
    'Beauty', 'Skincare', 'Private Chef', 'Tattoo & Piercing',
    'Pet Grooming', 'Photography', 'Tutoring', 'Other'
]

def slugify(text):
    text = re.sub(r'[^\w\s-]', '', text.lower().strip())
    return re.sub(r'[\s_-]+', '-', text)[:50]

@auth_bp.route('/')
def landing():
    from billing import PLAN_PRICE
    return render_template('landing.html', price=PLAN_PRICE)

@auth_bp.route('/privacy')
def privacy():
    return render_template('privacy.html')

@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard.index'))

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        slug = slugify(request.form.get('slug', '') or name)
        email = request.form.get('email', '').strip().lower()
        from db import normalize_phone
        phone = normalize_phone(request.form.get('phone', '').strip())
        password = request.form.get('password', '')
        password_confirm = request.form.get('password_confirm', '')
        category = request.form.get('category', '').strip()

        if not all([name, slug, email, phone, password, category]):
            flash('flash.auth.required_fields', 'error')
            return render_template('auth/register.html', form=request.form, categories=CATEGORIES)
        if len(password) < 6:
            flash('flash.auth.password_min', 'error')
            return render_template('auth/register.html', form=request.form, categories=CATEGORIES)
        if password != password_confirm:
            flash('flash.auth.password_mismatch', 'error')
            return render_template('auth/register.html', form=request.form, categories=CATEGORIES)
        if len(phone) != 10:
            flash('flash.common.phone_invalid', 'error')
            return render_template('auth/register.html', form=request.form, categories=CATEGORIES)

        db = get_db()
        if db.execute('SELECT id FROM businesses WHERE slug=%s', (slug,)).fetchone():
            flash('flash.auth.slug_taken', 'error')
            db.close()
            return render_template('auth/register.html', form=request.form, categories=CATEGORIES)
        if db.execute('SELECT id FROM businesses WHERE email=%s', (email,)).fetchone():
            flash('flash.auth.email_taken', 'error')
            db.close()
            return render_template('auth/register.html', form=request.form, categories=CATEGORIES)

        db.execute(
            "INSERT INTO businesses (name, slug, email, password_hash, phone, category, is_approved, trial_ends_at, subscription_status) "
            "VALUES (%s,%s,%s,%s,%s,%s,0, NOW() + INTERVAL '30 days', 'trialing')",
            (name, slug, email, generate_password_hash(password), phone, category)
        )
        db.commit()

        biz = db.execute('SELECT id FROM businesses WHERE email=%s', (email,)).fetchone()
        defaults = [
            (0,'09:00','18:00',0),(1,'09:00','18:00',0),(2,'09:00','18:00',0),
            (3,'09:00','18:00',0),(4,'09:00','18:00',0),(5,'09:00','17:00',0),(6,'09:00','17:00',1),
        ]
        for wd, ot, ct, closed in defaults:
            db.execute(
                'INSERT INTO business_hours (business_id, weekday, open_time, close_time, is_closed) VALUES (%s,%s,%s,%s,%s)',
                (biz['id'], wd, ot, ct, closed)
            )
        db.commit()
        db.close()

        flash('flash.auth.register_success', 'success')
        return redirect(url_for('auth.login'))

    return render_template('auth/register.html', form={}, categories=CATEGORIES)

@auth_bp.route('/login', methods=['GET', 'POST'])
@limiter.limit('10 per minute')
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard.index'))

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        db = get_db()
        row = db.execute('SELECT * FROM businesses WHERE email=%s', (email,)).fetchone()
        db.close()

        if not row or not check_password_hash(row['password_hash'], password):
            flash('flash.auth.login_invalid', 'error')
            return render_template('auth/login.html', email=email)

        if row.get('is_approved') != 1:
            flash('flash.auth.pending_approval', 'error')
            return render_template('auth/login.html', email=email)

        login_user(Business(row))
        return redirect(url_for('dashboard.index'))

    return render_template('auth/login.html')

@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('auth.landing'))

def _issue_reset_token(db, business_id):
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(hours=1)
    db.execute('UPDATE password_reset_tokens SET used=1 WHERE business_id=%s AND used=0', (business_id,))
    db.execute(
        'INSERT INTO password_reset_tokens (business_id, token, expires_at) VALUES (%s,%s,%s)',
        (business_id, token, expires)
    )
    db.commit()
    return token

@auth_bp.route('/forgot-password', methods=['GET', 'POST'])
@limiter.limit('5 per hour')
def forgot_password():
    if request.method == 'POST':
        method = request.form.get('method', 'email')
        db = get_db()
        if method == 'phone':
            from blueprints.booking import format_phone, send_sms
            raw = request.form.get('phone', '').strip()
            last10 = re.sub(r'\D', '', raw)[-10:]
            row = None
            if len(last10) == 10:
                row = db.execute(
                    "SELECT * FROM businesses WHERE RIGHT(regexp_replace(phone,'\\D','','g'),10)=%s",
                    (last10,)
                ).fetchone()
            if row:
                token = _issue_reset_token(db, row['id'])
                _base = os.environ.get('BASE_URL', request.host_url).rstrip('/')
                reset_url = f"{_base}{url_for('auth.reset_password', token=token)}"
                _msg = (
                    f'【Hastrid Booking】重置密码链接（1小时内有效）：{reset_url}\n\n'
                    f'[Hastrid Booking] Reset your password (link valid for 1 hour): {reset_url}'
                )
                threading.Thread(target=send_sms, args=(format_phone(raw), _msg), daemon=True).start()
            db.close()
            flash('flash.auth.reset_sent_phone', 'success')
            return redirect(url_for('auth.login'))
        else:
            email = request.form.get('email', '').strip().lower()
            row = db.execute('SELECT * FROM businesses WHERE email=%s', (email,)).fetchone()
            if row:
                token = _issue_reset_token(db, row['id'])
                _base = os.environ.get('BASE_URL', request.host_url).rstrip('/')
                reset_url = f"{_base}{url_for('auth.reset_password', token=token)}"
                _body = f'你好，\n\n点击以下链接重置密码（1小时内有效）：\n\n{reset_url}\n\n如果不是你本人操作，请忽略此邮件。'
                threading.Thread(target=send_email, args=(email, '重置你的 Hastrid Booking 密码', _body), daemon=True).start()
            db.close()
            flash('flash.auth.reset_sent_email', 'success')
            return redirect(url_for('auth.login'))
    return render_template('auth/forgot_password.html')

@auth_bp.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    db = get_db()
    row = db.execute(
        "SELECT * FROM password_reset_tokens WHERE token=%s AND used=0 AND expires_at > NOW()",
        (token,)
    ).fetchone()
    if not row:
        db.close()
        flash('flash.auth.reset_expired', 'error')
        return redirect(url_for('auth.forgot_password'))

    if request.method == 'POST':
        password = request.form.get('password', '')
        password_confirm = request.form.get('password_confirm', '')
        if len(password) < 6:
            db.close()
            flash('flash.auth.password_min', 'error')
            return render_template('auth/reset_password.html', token=token)
        if password != password_confirm:
            db.close()
            flash('flash.auth.password_mismatch', 'error')
            return render_template('auth/reset_password.html', token=token)
        db.execute(
            'UPDATE businesses SET password_hash=%s WHERE id=%s',
            (generate_password_hash(password), row['business_id'])
        )
        db.execute('UPDATE password_reset_tokens SET used=1 WHERE token=%s', (token,))
        db.commit()
        db.close()
        flash('flash.auth.reset_success', 'success')
        return redirect(url_for('auth.login'))

    db.close()
    return render_template('auth/reset_password.html', token=token)

@auth_bp.route('/explore')
def explore():
    from db import get_db
    cat = request.args.get('cat', '')
    db = get_db()
    if cat and cat in CATEGORIES:
        rows = db.execute(
            '''SELECT b.*, COUNT(s.id) as service_count
               FROM businesses b
               LEFT JOIN services s ON s.business_id = b.id AND s.is_active = 1
               WHERE b.category = %s
               GROUP BY b.id
               HAVING COUNT(s.id) > 0
               ORDER BY b.name''',
            (cat,)
        ).fetchall()
    else:
        rows = db.execute(
            '''SELECT b.*, COUNT(s.id) as service_count
               FROM businesses b
               LEFT JOIN services s ON s.business_id = b.id AND s.is_active = 1
               GROUP BY b.id
               HAVING COUNT(s.id) > 0
               ORDER BY b.name'''
        ).fetchall()
    db.close()
    return render_template('explore.html', businesses=rows, categories=CATEGORIES, active_cat=cat)

from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from db import get_db
from models import Business
import re

auth_bp = Blueprint('auth', __name__)

def slugify(text):
    text = re.sub(r'[^\w\s-]', '', text.lower().strip())
    return re.sub(r'[\s_-]+', '-', text)[:50]

@auth_bp.route('/')
def landing():
    return render_template('landing.html')

@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard.index'))

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        slug = slugify(request.form.get('slug', '') or name)
        email = request.form.get('email', '').strip().lower()
        phone = request.form.get('phone', '').strip()
        password = request.form.get('password', '')

        if not all([name, slug, email, phone, password]):
            flash('All fields are required.', 'error')
            return render_template('auth/register.html', form=request.form)
        if len(password) < 6:
            flash('Password must be at least 6 characters.', 'error')
            return render_template('auth/register.html', form=request.form)

        db = get_db()
        if db.execute('SELECT id FROM businesses WHERE slug=%s', (slug,)).fetchone():
            flash('That URL is already taken.', 'error')
            db.close()
            return render_template('auth/register.html', form=request.form)
        if db.execute('SELECT id FROM businesses WHERE email=%s', (email,)).fetchone():
            flash('Email already registered.', 'error')
            db.close()
            return render_template('auth/register.html', form=request.form)

        db.execute(
            'INSERT INTO businesses (name, slug, email, password_hash, phone) VALUES (%s,%s,%s,%s,%s)',
            (name, slug, email, generate_password_hash(password), phone)
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

        flash('Account created! Please log in.', 'success')
        return redirect(url_for('auth.login'))

    return render_template('auth/register.html', form={})

@auth_bp.route('/login', methods=['GET', 'POST'])
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
            flash('Invalid email or password.', 'error')
            return render_template('auth/login.html')

        login_user(Business(row))
        return redirect(url_for('dashboard.index'))

    return render_template('auth/login.html')

@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('auth.landing'))

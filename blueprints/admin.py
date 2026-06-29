from flask import Blueprint, render_template, request, redirect, url_for, session, jsonify
from db import get_db
import os

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

def _check_secret():
    secret = os.environ.get('ADMIN_SECRET', '')
    return secret and session.get('admin_authed') == secret

@admin_bp.route('/', methods=['GET', 'POST'])
def index():
    secret = os.environ.get('ADMIN_SECRET', '')
    if not secret:
        return '未配置 ADMIN_SECRET 环境变量', 500

    if request.method == 'POST':
        entered = request.form.get('secret', '')
        if entered == secret:
            session['admin_authed'] = secret
            return redirect(url_for('admin.index'))
        return render_template('admin/index.html', error='密码错误', merchants=None)

    if not _check_secret():
        return render_template('admin/index.html', error=None, merchants=None)

    db = get_db()
    try:
        merchants = db.execute(
            "SELECT id, name, email, phone, category, is_approved, created_at FROM businesses ORDER BY is_approved ASC, created_at DESC"
        ).fetchall()
        return render_template('admin/index.html', error=None, merchants=[dict(m) for m in merchants])
    finally:
        db.close()

@admin_bp.route('/merchants/<int:biz_id>/approve', methods=['POST'])
def approve(biz_id):
    if not _check_secret():
        return redirect(url_for('admin.index'))
    db = get_db()
    try:
        db.execute('UPDATE businesses SET is_approved=1 WHERE id=%s', (biz_id,))
        db.commit()
    finally:
        db.close()
    return redirect(url_for('admin.index'))

@admin_bp.route('/merchants/<int:biz_id>/reject', methods=['POST'])
def reject(biz_id):
    if not _check_secret():
        return redirect(url_for('admin.index'))
    db = get_db()
    try:
        db.execute('UPDATE businesses SET is_approved=-1 WHERE id=%s', (biz_id,))
        db.commit()
    finally:
        db.close()
    return redirect(url_for('admin.index'))

@admin_bp.route('/logout')
def logout():
    session.pop('admin_authed', None)
    return redirect(url_for('admin.index'))

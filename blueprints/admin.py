from flask import Blueprint, render_template, request, redirect, url_for, session, jsonify
from db import get_db
from extensions import limiter
from translations import t
import os

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

def _check_secret():
    secret = os.environ.get('ADMIN_SECRET', '')
    return secret and session.get('admin_authed') == secret

@admin_bp.route('/', methods=['GET', 'POST'])
@limiter.limit('10 per minute; 30 per hour', methods=['POST'])
def index():
    secret = os.environ.get('ADMIN_SECRET', '')
    if not secret:
        return 'ADMIN_SECRET environment variable is not set', 500

    if request.method == 'POST':
        entered = request.form.get('secret', '')
        if entered == secret:
            session['admin_authed'] = secret
            return redirect(url_for('admin.index'))
        return render_template('admin/index.html', error=t('admin.wrong_password'), merchants=None)

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

@admin_bp.route('/feedback')
def feedback_inbox():
    if not _check_secret():
        return redirect(url_for('admin.index'))
    db = get_db()
    try:
        rows = db.execute(
            "SELECT f.*, b.name as biz_name FROM platform_feedback f "
            "LEFT JOIN businesses b ON f.business_id = b.id "
            "ORDER BY (f.status = 'new') DESC, f.created_at DESC"
        ).fetchall()
        return render_template('admin/feedback.html', items=[dict(r) for r in rows])
    finally:
        db.close()

@admin_bp.route('/feedback/<int:fid>/resolve', methods=['POST'])
def feedback_resolve(fid):
    if not _check_secret():
        return redirect(url_for('admin.index'))
    db = get_db()
    try:
        db.execute("UPDATE platform_feedback SET status='resolved' WHERE id=%s", (fid,))
        db.commit()
    finally:
        db.close()
    return redirect(url_for('admin.feedback_inbox'))

@admin_bp.route('/broadcasts')
def broadcasts():
    if not _check_secret():
        return redirect(url_for('admin.index'))
    db = get_db()
    try:
        rows = db.execute(
            "SELECT br.*, b.name as biz_name FROM broadcast_requests br "
            "LEFT JOIN businesses b ON br.business_id = b.id "
            "ORDER BY (br.status = 'pending') DESC, br.created_at DESC"
        ).fetchall()
        return render_template('admin/broadcasts.html', items=[dict(r) for r in rows])
    finally:
        db.close()

@admin_bp.route('/broadcasts/<int:bid>/approve', methods=['POST'])
def broadcast_approve(bid):
    if not _check_secret():
        return redirect(url_for('admin.index'))
    import json, threading
    from blueprints.booking import send_sms
    db = get_db()
    try:
        row = db.execute(
            "SELECT br.*, b.name as biz_name FROM broadcast_requests br "
            "LEFT JOIN businesses b ON br.business_id=b.id WHERE br.id=%s", (bid,)
        ).fetchone()
        if not row or row['status'] != 'pending':
            return redirect(url_for('admin.broadcasts'))
        try:
            phones = json.loads(row['phones'])
        except Exception:
            phones = []
        body = row['message'] + '\n\n【' + (row['biz_name'] or '') + '】回复 STOP 退订'
        db.execute(
            "UPDATE broadcast_requests SET status='approved', sent_count=%s, reviewed_at=NOW() WHERE id=%s",
            (len(phones), bid)
        )
        db.commit()
    finally:
        db.close()

    def _send_all(nums, text):
        for n in nums:
            send_sms(n, text)
    threading.Thread(target=_send_all, args=(phones, body), daemon=True).start()
    return redirect(url_for('admin.broadcasts'))

@admin_bp.route('/broadcasts/<int:bid>/reject', methods=['POST'])
def broadcast_reject(bid):
    if not _check_secret():
        return redirect(url_for('admin.index'))
    db = get_db()
    try:
        db.execute("UPDATE broadcast_requests SET status='rejected', reviewed_at=NOW() WHERE id=%s AND status='pending'", (bid,))
        db.commit()
    finally:
        db.close()
    return redirect(url_for('admin.broadcasts'))

@admin_bp.route('/logout')
def logout():
    session.pop('admin_authed', None)
    return redirect(url_for('admin.index'))

"""创建/重置一个数据丰富的英文 DEMO 商家号，用于向英文客户展示各页面功能。可反复跑（每次重置该号数据）。

用法：
  生产库： DATABASE_URL='<Railway DATABASE_PUBLIC_URL>' python scripts/seed_demo_en.py
  本地库： python scripts/seed_demo_en.py

DEMO 登录： email = demo@bloomstudio.com    password = demo1234
"""
import sys, os, uuid, random
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import get_db
from werkzeug.security import generate_password_hash

EMAIL, PASSWORD, NAME, SLUG = 'demo@bloomstudio.com', 'demo1234', 'Bloom Nail & Beauty Studio', 'bloom-demo'
random.seed(7)
db = get_db()

# ---------- 商家（存在则重置其全部子数据） ----------
row = db.execute('SELECT id FROM businesses WHERE email=%s', (EMAIL,)).fetchone()
if row:
    BIZ = row['id']
    db.execute('DELETE FROM customer_photos WHERE customer_id IN (SELECT id FROM customers WHERE business_id=%s)', (BIZ,))
    db.execute('DELETE FROM balance_transactions WHERE customer_id IN (SELECT id FROM customers WHERE business_id=%s)', (BIZ,))
    db.execute('DELETE FROM appointments WHERE business_id=%s', (BIZ,))
    db.execute('DELETE FROM customers WHERE business_id=%s', (BIZ,))
    db.execute('DELETE FROM staff_services WHERE staff_id IN (SELECT id FROM staff WHERE business_id=%s)', (BIZ,))
    db.execute('DELETE FROM staff_hours WHERE staff_id IN (SELECT id FROM staff WHERE business_id=%s)', (BIZ,))
    db.execute('DELETE FROM staff WHERE business_id=%s', (BIZ,))
    db.execute('DELETE FROM services WHERE business_id=%s', (BIZ,))
    db.execute('DELETE FROM time_blocks WHERE business_id=%s', (BIZ,))
    db.execute('DELETE FROM business_blackouts WHERE business_id=%s', (BIZ,))
    db.execute('DELETE FROM business_hours WHERE business_id=%s', (BIZ,))
    db.execute("UPDATE businesses SET name=%s, password_hash=%s, is_approved=1, phone=%s, address=%s, description=%s, "
               "category=%s, avatar_url=%s, cover_url=%s, calendar_token=%s, subscription_status='comp', "
               "trial_ends_at=NOW()+INTERVAL '2 years' WHERE id=%s",
               (NAME, generate_password_hash(PASSWORD), '9095550199', '9450 Foothill Blvd, Rancho Cucamonga, CA',
                'Nails, lashes and pedicures done right, in a relaxed studio that runs on word of mouth', 'Nails',
                'https://picsum.photos/seed/bloomAva/400/400', 'https://picsum.photos/seed/bloomCov/1200/400',
                str(uuid.uuid4()), BIZ))
else:
    BIZ = db.execute(
        'INSERT INTO businesses (name, slug, email, password_hash, phone, address, description, category, '
        'avatar_url, cover_url, api_token, calendar_token, is_approved, subscription_status, trial_ends_at) '
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1,'comp',NOW()+INTERVAL '2 years') RETURNING id",
        (NAME, SLUG, EMAIL, generate_password_hash(PASSWORD), '9095550199', '9450 Foothill Blvd, Rancho Cucamonga, CA',
         'Nails, lashes and pedicures done right, in a relaxed studio that runs on word of mouth', 'Nails',
         'https://picsum.photos/seed/bloomAva/400/400', 'https://picsum.photos/seed/bloomCov/1200/400',
         str(uuid.uuid4()), str(uuid.uuid4()))
    ).fetchone()['id']
db.commit()

# ---------- 营业时间（周一0..周日6） ----------
for wd in range(7):
    ot, ct, closed = ('11:00', '17:00', 0) if wd == 6 else ('10:00', '19:00', 0)
    db.execute('INSERT INTO business_hours (business_id, weekday, open_time, close_time, is_closed) VALUES (%s,%s,%s,%s,%s)',
               (BIZ, wd, ot, ct, closed))

# ---------- 服务 ----------
services = [
    ('Classic Manicure', 'Hands · Solid color', 30, 35, '💅', '#E5634D'),
    ('French Manicure', 'Classic style', 60, 65, '💅', '#C9A84C'),
    ('Lash Extensions', 'Natural look', 90, 120, '👁', '#9B5DE5'),
    ('Mani-Pedi Combo', 'Deep care', 120, 150, '🦶', '#4CA86B'),
    ('Polish Removal', 'Quick', 15, 15, '✂️', '#4C86C9'),
]
svc = []  # (id, price)
for i, (n, sub, dur, price, emoji, color) in enumerate(services):
    sid = db.execute(
        'INSERT INTO services (business_id, name, name_sub, duration_mins, price, emoji, color, is_active, sort_order) '
        'VALUES (%s,%s,%s,%s,%s,%s,%s,1,%s) RETURNING id',
        (BIZ, n, sub, dur, price, emoji, color, i)).fetchone()['id']
    svc.append((sid, price))
db.commit()

# ---------- 员工 + 排班 + 可做服务 ----------
staff_defs = [
    ('Mia', '🙎‍♀️', 'Senior nail tech, 8 years of experience', [0, 1, 3, 4]),
    ('Kayla', '🙆‍♀️', 'Lash specialist, gentle technique', [2, 4]),
]
staff_ids = []
for name, emoji, bio, svc_idx in staff_defs:
    stid = db.execute('INSERT INTO staff (business_id, name, emoji, bio, is_active, sort_order) VALUES (%s,%s,%s,%s,1,%s) RETURNING id',
                      (BIZ, name, emoji, bio, len(staff_ids))).fetchone()['id']
    staff_ids.append(stid)
    for wd in range(7):
        closed = 1 if wd == 6 else 0
        db.execute('INSERT INTO staff_hours (staff_id, weekday, open_time, close_time, is_closed) VALUES (%s,%s,%s,%s,%s)',
                   (stid, wd, '10:00', '19:00', closed))
    for idx in svc_idx:
        db.execute('INSERT INTO staff_services (staff_id, service_id) VALUES (%s,%s)', (stid, svc[idx][0]))
db.commit()

# ---------- 客户（偏好/储值/隐形备注/头像） ----------
customers = [
    ('9095551301', 'Sarah Johnson', 'Likes nude tones, ticklish', 'VIP, often brings friends', 200, 'https://picsum.photos/seed/bloomCust1/200/200'),
    ('9095551302', 'Emily Davis', 'Allergic skin, avoid a certain polish remover brand', 'Short-tempered, work fast', 80, 'https://picsum.photos/seed/bloomCust2/200/200'),
    ('9095551303', 'Michael Lee', 'Likes french tips, chatty', 'Comes in like clockwork every month', 50, ''),
    ('9095551304', 'Olivia Brown', '', 'First time here, still deciding if she likes us', 0, ''),
    ('9095551305', 'Ava Martinez', 'Sensitive skin on hands', 'Complained about the wait last time, keep her happy', -20, ''),
    ('9095551306', 'Isabella Kim', 'Likes glitter and color-block designs', 'Instagram influencer, always takes photos', 120, 'https://picsum.photos/seed/bloomCust6/200/200'),
    ('9095551307', 'Grace Turner', 'Prefers minimalist looks', 'Student, tight budget', 30, ''),
]
cust = []  # (id, name, phone)
for phone, name, pref, note, bal, avatar in customers:
    cid = db.execute(
        'INSERT INTO customers (business_id, phone, name, preferences, private_note, balance, avatar_url, profile_token) '
        'VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id',
        (BIZ, phone, name, pref, note, bal, avatar, str(uuid.uuid4()))).fetchone()['id']
    cust.append((cid, name, phone))
    if bal != 0:
        db.execute('INSERT INTO balance_transactions (customer_id, delta, reason) VALUES (%s,%s,%s)',
                   (cid, bal, 'Balance top-up' if bal > 0 else 'Overdraft charge'))
db.commit()

# ---------- 客户参考照片 ----------
for cid, seed in [(cust[0][0], 'bloomNailA'), (cust[0][0], 'bloomNailB'), (cust[5][0], 'bloomNailC')]:
    db.execute("INSERT INTO customer_photos (customer_id, photo_url, note, uploaded_by) VALUES (%s,%s,%s,'merchant')",
               (cid, f'https://picsum.photos/seed/{seed}/600/600', 'Style from last time'))
db.commit()

# ---------- 预约：过往到未来，各状态，聚集高峰时段 ----------
now = datetime.now()
peak_hours = [10, 10, 11, 11, 13, 14, 14, 15, 16]
notes = ['', '', 'Client might be 10 min late', '', 'Bringing a friend', '']
apt = 0
for off in range(-35, 15):
    d = (now + timedelta(days=off)).date()
    for _ in range(random.choice([0, 1, 1, 2, 2, 3])):
        h, mi = random.choice(peak_hours), random.choice([0, 0, 30])
        sid, price = random.choice(svc)
        cid, cname, cphone = random.choice(cust)
        stid = random.choice(staff_ids)
        status = 'cancelled' if (off < 0 and random.random() < 0.12) else 'confirmed'
        db.execute(
            'INSERT INTO appointments (business_id, service_id, customer_name, phone, appointment_dt, comment, '
            'status, cancel_token, staff_id, customer_id, merchant_note) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
            (BIZ, sid, cname, cphone, f'{d} {h:02d}:{mi:02d}', random.choice(notes), status,
             str(uuid.uuid4()), stid, cid, random.choice(['', '', 'Regular client, knows the routine'])))
        apt += 1
db.commit()

# ---------- 时段锁定 + 休业 ----------
today = now.date()
db.execute('INSERT INTO time_blocks (business_id, staff_id, date, start_time, end_time, reason) VALUES (%s,NULL,%s,%s,%s,%s)',
           (BIZ, str(today), '12:00', '13:00', 'Lunch break'))
db.execute('INSERT INTO time_blocks (business_id, staff_id, date, start_time, end_time, reason) VALUES (%s,%s,%s,%s,%s,%s)',
           (BIZ, staff_ids[1], str(today + timedelta(days=1)), '10:00', '12:00', 'Kayla out for training'))
hol = today + timedelta(days=9)
db.execute('INSERT INTO business_blackouts (business_id, start_date, end_date, reason) VALUES (%s,%s,%s,%s)',
           (BIZ, str(hol), str(hol + timedelta(days=1)), 'Closed 2 days for anniversary'))
db.commit()

# ---------- 平台反馈样例 ----------
db.execute("INSERT INTO platform_feedback (source, business_id, name, contact, message, status) VALUES (%s,%s,%s,%s,%s,%s)",
           ('merchant', BIZ, 'Demo Owner', EMAIL, 'Would love a staff commission tracking feature', 'new'))
db.commit()
db.close()

print(f'✅ DEMO 商家已就绪  business_id={BIZ}')
print(f'   登录：{EMAIL} / {PASSWORD}')
print(f'   服务 {len(svc)} · 员工 {len(staff_ids)} · 客户 {len(cust)} · 预约 {apt} 条 · 锁定/休业已建')

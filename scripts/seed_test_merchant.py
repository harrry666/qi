"""创建/重置一个数据丰富的 DEMO 商家号，用于全面展示各页面功能。可反复跑（每次重置该号数据）。

用法：
  生产库： DATABASE_URL='<Railway DATABASE_PUBLIC_URL>' python scripts/seed_test_merchant.py
  本地库： python scripts/seed_test_merchant.py

DEMO 登录： email = demo@hagua.com    password = demo1234
"""
import sys, os, uuid, random
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import get_db
from werkzeug.security import generate_password_hash

EMAIL, PASSWORD, NAME, SLUG = 'demo@hagua.com', 'demo1234', '哈瓜美甲工作室', 'hagua-demo'
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
               "category=%s, avatar_url=%s, cover_url=%s, calendar_token=%s WHERE id=%s",
               (NAME, generate_password_hash(PASSWORD), '9095550188', '9200 Foothill Blvd, Rancho Cucamonga',
                '专业美甲 · 美睫 · 手足护理，环境优雅、只做熟客口碑', '美甲',
                'https://picsum.photos/seed/haguaAva/400/400', 'https://picsum.photos/seed/haguaCov/1200/400',
                str(uuid.uuid4()), BIZ))
else:
    BIZ = db.execute(
        'INSERT INTO businesses (name, slug, email, password_hash, phone, address, description, category, '
        'avatar_url, cover_url, api_token, calendar_token, is_approved) '
        'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1) RETURNING id',
        (NAME, SLUG, EMAIL, generate_password_hash(PASSWORD), '9095550188', '9200 Foothill Blvd, Rancho Cucamonga',
         '专业美甲 · 美睫 · 手足护理，环境优雅、只做熟客口碑', '美甲',
         'https://picsum.photos/seed/haguaAva/400/400', 'https://picsum.photos/seed/haguaCov/1200/400',
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
    ('基础美甲', '手部 · 单色', 30, 35, '💅', '#E5634D'),
    ('法式美甲', '经典款', 60, 65, '💅', '#C9A84C'),
    ('美睫嫁接', '自然款', 90, 120, '👁', '#9B5DE5'),
    ('手足护理套餐', '深层保养', 120, 150, '🦶', '#4CA86B'),
    ('卸甲', '快速', 15, 15, '✂️', '#4C86C9'),
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
    ('小美', '🙎‍♀️', '资深美甲师，8年经验', [0, 1, 3, 4]),
    ('小琪', '🙆‍♀️', '美睫专家，手法轻柔', [2, 4]),
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
    ('9095551201', 'Jessica Chen', '喜欢裸色系，怕痒', 'VIP，常带闺蜜来', 200, 'https://picsum.photos/seed/cust1/200/200'),
    ('9095551202', '王丽', '过敏体质，勿用某品牌卸甲水', '脾气急，动作要快', 80, 'https://picsum.photos/seed/cust2/200/200'),
    ('9095551203', 'Amy Liu', '喜欢法式，爱聊天', '每月固定来一次', 50, ''),
    ('9095551204', '陈静', '', '第一次来，还在观察', 0, ''),
    ('9095551205', '张梅', '手部皮肤敏感', '上次抱怨等太久，注意安抚', -20, ''),
    ('9095551206', 'Grace Wu', '偏好亮片、跳色', '小红书博主，会拍照打卡', 120, 'https://picsum.photos/seed/cust6/200/200'),
    ('9095551207', '林小满', '喜欢简约风', '学生，预算有限', 30, ''),
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
                   (cid, bal, '储值充值' if bal > 0 else '透支消费'))
db.commit()

# ---------- 客户参考照片 ----------
for cid, seed in [(cust[0][0], 'nailA'), (cust[0][0], 'nailB'), (cust[5][0], 'nailC')]:
    db.execute("INSERT INTO customer_photos (customer_id, photo_url, note, uploaded_by) VALUES (%s,%s,%s,'merchant')",
               (cid, f'https://picsum.photos/seed/{seed}/600/600', '上次做的款式'))
db.commit()

# ---------- 预约：过往到未来，各状态，聚集高峰时段 ----------
now = datetime.now()
peak_hours = [10, 10, 11, 11, 13, 14, 14, 15, 16]
notes = ['', '', '客人会迟到 10 分钟', '', '带朋友一起', '']
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
             str(uuid.uuid4()), stid, cid, random.choice(['', '', '老客，熟练操作'])))
        apt += 1
db.commit()

# ---------- 时段锁定 + 休业 ----------
today = now.date()
db.execute('INSERT INTO time_blocks (business_id, staff_id, date, start_time, end_time, reason) VALUES (%s,NULL,%s,%s,%s,%s)',
           (BIZ, str(today), '12:00', '13:00', '午休'))
db.execute('INSERT INTO time_blocks (business_id, staff_id, date, start_time, end_time, reason) VALUES (%s,%s,%s,%s,%s,%s)',
           (BIZ, staff_ids[1], str(today + timedelta(days=1)), '10:00', '12:00', '小琪外出培训'))
hol = today + timedelta(days=9)
db.execute('INSERT INTO business_blackouts (business_id, start_date, end_date, reason) VALUES (%s,%s,%s,%s)',
           (BIZ, str(hol), str(hol + timedelta(days=1)), '店庆闭店 2 天'))
db.commit()

# ---------- 平台反馈样例 ----------
db.execute("INSERT INTO platform_feedback (source, business_id, name, contact, message, status) VALUES (%s,%s,%s,%s,%s,%s)",
           ('merchant', BIZ, 'Demo 店主', EMAIL, '希望能加员工提成统计', 'new'))
db.commit()
db.close()

print(f'✅ DEMO 商家已就绪  business_id={BIZ}')
print(f'   登录：{EMAIL} / {PASSWORD}')
print(f'   服务 {len(svc)} · 员工 {len(staff_ids)} · 客户 {len(cust)} · 预约 {apt} 条 · 锁定/休业已建')

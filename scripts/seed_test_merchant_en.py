"""Create/reset an English-only DEMO merchant, fully stocked with data, for demoing the product to
English-speaking prospects. Safe to re-run (resets this account's data each time).

Usage:
  Production DB: DATABASE_URL='<Railway DATABASE_PUBLIC_URL>' python scripts/seed_test_merchant_en.py
  Local DB:      python scripts/seed_test_merchant_en.py

DEMO login: email = demo-en@hastrid.app    password = demo1234
"""
import sys, os, uuid, random
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import get_db
from werkzeug.security import generate_password_hash

EMAIL, PASSWORD, NAME, SLUG = 'demo-en@hastrid.app', 'demo1234', 'Glow Nail & Lash Studio', 'glow-demo'
random.seed(11)
db = get_db()

# ---------- Business (reset all child data if it already exists) ----------
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
               (NAME, generate_password_hash(PASSWORD), '6265551034', '210 W Main St, Alhambra, CA',
                'Modern nail & lash studio. Clean, relaxing space, by-appointment only.', 'Nails',
                'https://picsum.photos/seed/glowAva/400/400', 'https://picsum.photos/seed/glowCov/1200/400',
                str(uuid.uuid4()), BIZ))
else:
    BIZ = db.execute(
        'INSERT INTO businesses (name, slug, email, password_hash, phone, address, description, category, '
        'avatar_url, cover_url, api_token, calendar_token, is_approved) '
        'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1) RETURNING id',
        (NAME, SLUG, EMAIL, generate_password_hash(PASSWORD), '6265551034', '210 W Main St, Alhambra, CA',
         'Modern nail & lash studio. Clean, relaxing space, by-appointment only.', 'Nails',
         'https://picsum.photos/seed/glowAva/400/400', 'https://picsum.photos/seed/glowCov/1200/400',
         str(uuid.uuid4()), str(uuid.uuid4()))
    ).fetchone()['id']
db.commit()

# ---------- Business hours (Mon=0 .. Sun=6) ----------
for wd in range(7):
    ot, ct, closed = ('11:00', '17:00', 0) if wd == 6 else ('10:00', '19:00', 0)
    db.execute('INSERT INTO business_hours (business_id, weekday, open_time, close_time, is_closed) VALUES (%s,%s,%s,%s,%s)',
               (BIZ, wd, ot, ct, closed))

# ---------- Services ----------
services = [
    ('Classic Manicure', 'Hands · Single color', 30, 35, '💅', '#E5634D'),
    ('French Manicure', 'Classic tips', 60, 65, '💅', '#C9A84C'),
    ('Lash Extensions', 'Natural set', 90, 120, '👁', '#9B5DE5'),
    ('Mani-Pedi Package', 'Deep-care combo', 120, 150, '🦶', '#4CA86B'),
    ('Polish Removal', 'Quick soak-off', 15, 15, '✂️', '#4C86C9'),
]
svc = []  # (id, price)
for i, (n, sub, dur, price, emoji, color) in enumerate(services):
    sid = db.execute(
        'INSERT INTO services (business_id, name, name_sub, duration_mins, price, emoji, color, is_active, sort_order) '
        'VALUES (%s,%s,%s,%s,%s,%s,%s,1,%s) RETURNING id',
        (BIZ, n, sub, dur, price, emoji, color, i)).fetchone()['id']
    svc.append((sid, price))
db.commit()

# ---------- Staff + schedules + services they can perform ----------
staff_defs = [
    ('Mia', '🙎‍♀️', 'Senior nail tech, 8 years experience', [0, 1, 3, 4]),
    ('Chloe', '🙆‍♀️', 'Lash specialist, gentle technique', [2, 4]),
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

# ---------- Customers (preferences / store credit / private notes / avatars) ----------
customers = [
    ('6265551201', 'Jessica Chen', 'Likes nude tones, ticklish feet', 'VIP, often brings friends', 200, 'https://picsum.photos/seed/custE1/200/200'),
    ('6265551202', 'Emily Park', 'Sensitive skin, avoid a certain acetone brand', 'Impatient, work fast', 80, 'https://picsum.photos/seed/custE2/200/200'),
    ('6265551203', 'Amy Liu', 'Likes French tips, chatty', 'Comes in monthly like clockwork', 50, ''),
    ('6265551204', 'Sarah Kim', '', 'First-time customer, still deciding', 0, ''),
    ('6265551205', 'Megan Ross', 'Sensitive hands', 'Complained about wait time last visit, reassure her', -20, ''),
    ('6265551206', 'Grace Wu', 'Loves glitter and ombre', 'Instagram influencer, will post photos', 120, 'https://picsum.photos/seed/custE6/200/200'),
    ('6265551207', 'Olivia Martin', 'Prefers minimalist styles', 'Student, budget-conscious', 30, ''),
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
                   (cid, bal, 'Store credit top-up' if bal > 0 else 'Overdraft spend'))
db.commit()

# ---------- Customer reference photos ----------
for cid, seed in [(cust[0][0], 'nailEA'), (cust[0][0], 'nailEB'), (cust[5][0], 'nailEC')]:
    db.execute("INSERT INTO customer_photos (customer_id, photo_url, note, uploaded_by) VALUES (%s,%s,%s,'merchant')",
               (cid, f'https://picsum.photos/seed/{seed}/600/600', 'Style from last visit'))
db.commit()

# ---------- Appointments: past through future, mixed statuses, clustered at peak hours ----------
now = datetime.now()
peak_hours = [10, 10, 11, 11, 13, 14, 14, 15, 16]
notes = ['', '', 'Customer may be 10 min late', '', 'Bringing a friend', '']
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
             str(uuid.uuid4()), stid, cid, random.choice(['', '', 'Regular, knows the routine'])))
        apt += 1
db.commit()

# ---------- Time blocks + closures ----------
today = now.date()
db.execute('INSERT INTO time_blocks (business_id, staff_id, date, start_time, end_time, reason) VALUES (%s,NULL,%s,%s,%s,%s)',
           (BIZ, str(today), '12:00', '13:00', 'Lunch break'))
db.execute('INSERT INTO time_blocks (business_id, staff_id, date, start_time, end_time, reason) VALUES (%s,%s,%s,%s,%s,%s)',
           (BIZ, staff_ids[1], str(today + timedelta(days=1)), '10:00', '12:00', 'Chloe at a training'))
hol = today + timedelta(days=9)
db.execute('INSERT INTO business_blackouts (business_id, start_date, end_date, reason) VALUES (%s,%s,%s,%s)',
           (BIZ, str(hol), str(hol + timedelta(days=1)), 'Closed 2 days for anniversary'))
db.commit()

# ---------- Sample platform feedback ----------
db.execute("INSERT INTO platform_feedback (source, business_id, name, contact, message, status) VALUES (%s,%s,%s,%s,%s,%s)",
           ('merchant', BIZ, 'Demo Owner', EMAIL, 'Would love a staff commission report', 'new'))
db.commit()
db.close()

print(f'DEMO merchant ready  business_id={BIZ}')
print(f'   Login: {EMAIL} / {PASSWORD}')
print(f'   {len(svc)} services · {len(staff_ids)} staff · {len(cust)} customers · {apt} appointments · blocks/closures set')

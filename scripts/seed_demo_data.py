"""往 harry studio (business_id=3) 灌演示数据，跑一次即可。看完效果可以删掉这个脚本。"""
import sys, os, uuid
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import get_db, upsert_customer

BIZ_ID = 3

db = get_db()

svc_ids = [r['id'] for r in db.execute('SELECT id FROM services WHERE business_id=%s ORDER BY id', (BIZ_ID,)).fetchall()]
staff_ids = [r['id'] for r in db.execute('SELECT id FROM staff WHERE business_id=%s ORDER BY id', (BIZ_ID,)).fetchall()]
colors = ['#F5A3B8', '#A3C4F5', '#B8F5A3', '#F5D6A3', '#D6A3F5']
for i, sid in enumerate(svc_ids):
    db.execute('UPDATE services SET color=%s WHERE id=%s', (colors[i % len(colors)], sid))

customers = [
    ('4155551201', '王丽', '喜欢无味指甲油，怕痒', '客户脾气比较急，动作要快', 80),
    ('4155551202', 'Jessica Chen', '过敏体质，勿用XX品牌', 'VIP，每次都带闺蜜来', 200),
    ('4155551203', '陈静', '', '第一次来，还在观察', 0),
    ('4155551204', 'Amy Liu', '喜欢法式美甲', '老客户，每月固定来', 50),
    ('4155551205', '张梅', '手部皮肤敏感', '上次投诉过等待时间长，注意安抚', -20),
]

customer_ids = []
for phone, name, pref, note, balance in customers:
    cid = upsert_customer(db, BIZ_ID, phone, name)
    db.execute('UPDATE customers SET preferences=%s, private_note=%s, balance=%s WHERE id=%s',
               (pref, note, balance, cid))
    customer_ids.append((cid, name, phone))
db.commit()

for cid, name, phone in customer_ids:
    bal = db.execute('SELECT balance FROM customers WHERE id=%s', (cid,)).fetchone()['balance']
    if bal != 0:
        reason = '储值充值' if bal > 0 else '透支消费'
        db.execute('INSERT INTO balance_transactions (customer_id, delta, reason) VALUES (%s,%s,%s)',
                   (cid, bal, reason))
db.commit()

now = datetime.now()
statuses = ['confirmed', 'confirmed', 'confirmed', 'cancelled', 'confirmed']
for i, (cid, name, phone) in enumerate(customer_ids):
    svc_id = svc_ids[i % len(svc_ids)]
    staff_id = staff_ids[i % len(staff_ids)] if staff_ids else None
    dt = now + timedelta(days=(i - 2), hours=(10 + i))
    apt_dt = dt.strftime('%Y-%m-%d %H:%M')
    cancel_token = str(uuid.uuid4())
    db.execute(
        'INSERT INTO appointments (business_id, service_id, customer_name, phone, appointment_dt, comment, status, cancel_token, staff_id, customer_id) '
        'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
        (BIZ_ID, svc_id, name, phone, apt_dt, '', statuses[i % len(statuses)], cancel_token, staff_id, cid)
    )
db.commit()

db.execute(
    "INSERT INTO platform_feedback (source, business_id, name, contact, message, status) VALUES (%s,%s,%s,%s,%s,%s)",
    ('merchant', BIZ_ID, 'Harry', 'yaocan.yin@gmail.com', '希望能支持员工提成统计功能', 'new')
)
db.commit()

print(f'插入 {len(customer_ids)} 个客户档案，{len(customer_ids)} 条预约，1 条商家反馈。')
db.close()

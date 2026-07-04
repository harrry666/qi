"""给当前这周灌预约数据，让日历日程表看得出效果。"""
import sys, os, uuid, random
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import get_db

BIZ_ID = 3

db = get_db()
svc_ids = [r['id'] for r in db.execute('SELECT id FROM services WHERE business_id=%s ORDER BY id', (BIZ_ID,)).fetchall()]
staff_ids = [r['id'] for r in db.execute('SELECT id FROM staff WHERE business_id=%s ORDER BY id', (BIZ_ID,)).fetchall()]
customers = db.execute('SELECT id, name, phone FROM customers WHERE business_id=%s', (BIZ_ID,)).fetchall()

today = datetime(2026, 7, 2)
monday = today - timedelta(days=today.weekday())

names = ['王丽', 'Jessica Chen', '陈静', 'Amy Liu', '张梅', '李蕾', '周晨', '刘洋']
hours = [9, 10, 11, 13, 14, 15, 16, 17, 18]

random.seed(42)
count = 0
for day_offset in range(7):
    day = monday + timedelta(days=day_offset)
    n_apts = random.randint(1, 4)
    used_hours = random.sample(hours, min(n_apts, len(hours)))
    for h in used_hours:
        svc_id = random.choice(svc_ids)
        staff_id = random.choice(staff_ids) if staff_ids else None
        name = random.choice(names)
        phone = '415555' + str(1200 + names.index(name))
        minute = random.choice([0, 30])
        apt_dt = day.replace(hour=h, minute=minute).strftime('%Y-%m-%d %H:%M')
        status = 'cancelled' if random.random() < 0.1 else 'confirmed'
        cancel_token = str(uuid.uuid4())
        db.execute(
            'INSERT INTO appointments (business_id, service_id, customer_name, phone, appointment_dt, comment, status, cancel_token, staff_id) '
            'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)',
            (BIZ_ID, svc_id, name, phone, apt_dt, '', status, cancel_token, staff_id)
        )
        count += 1
db.commit()
print(f'本周（{monday.strftime("%Y-%m-%d")} 起）插入 {count} 条预约')
db.close()

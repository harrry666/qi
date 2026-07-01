"""删除商家及其全部关联数据（服务/营业时间/休业/预约/员工/排班/锁定时段/重置token）。
用法：DATABASE_URL='<生产库连接串>' python scripts/delete_business.py "Qi testing" "Chris"
按商家名匹配（不区分大小写），删除前打印将删除的商家，事务提交。
"""
import os
import sys
import psycopg2
import psycopg2.extras

url = os.environ.get('DATABASE_URL', '')
if url.startswith('postgres://'):
    url = url.replace('postgres://', 'postgresql://', 1)
if not url:
    print('缺少 DATABASE_URL 环境变量')
    sys.exit(1)

names = sys.argv[1:]
if not names:
    print('用法：python scripts/delete_business.py "商家名1" "商家名2" ...')
    sys.exit(1)

conn = psycopg2.connect(url)
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

for name in names:
    cur.execute('SELECT id, name, slug FROM businesses WHERE LOWER(name)=LOWER(%s)', (name,))
    rows = cur.fetchall()
    if not rows:
        print(f'× 未找到商家：{name}')
        continue
    for b in rows:
        bid = b['id']
        cur.execute('DELETE FROM staff_hours WHERE staff_id IN (SELECT id FROM staff WHERE business_id=%s)', (bid,))
        cur.execute('DELETE FROM staff_services WHERE staff_id IN (SELECT id FROM staff WHERE business_id=%s)', (bid,))
        cur.execute('DELETE FROM staff WHERE business_id=%s', (bid,))
        cur.execute('DELETE FROM time_blocks WHERE business_id=%s', (bid,))
        cur.execute('DELETE FROM appointments WHERE business_id=%s', (bid,))
        cur.execute('DELETE FROM business_hours WHERE business_id=%s', (bid,))
        cur.execute('DELETE FROM business_blackouts WHERE business_id=%s', (bid,))
        cur.execute('DELETE FROM services WHERE business_id=%s', (bid,))
        cur.execute('DELETE FROM password_reset_tokens WHERE business_id=%s', (bid,))
        cur.execute('DELETE FROM businesses WHERE id=%s', (bid,))
        print(f'✓ 已删除商家：{b["name"]}（id={bid}, slug={b["slug"]}）及全部关联数据')

conn.commit()
cur.close()
conn.close()
print('完成。')

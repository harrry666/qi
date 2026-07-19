#!/usr/bin/env python3
"""开一个合作院校，打印它的专属开通链接和看板链接。

用法：
    python scripts/add_school.py "VR Professional Beauty Academy" vr-professional
    DATABASE_URL='<生产库>' python scripts/add_school.py "学院名" slug

slug 用在毕业生注册链接里，token 是学院看板的只读凭证（自动生成，别外传）。
重复跑同一个 slug 不会重建，只会把已有的链接再打印一遍。
"""
import os
import sys
import uuid
import psycopg2
import psycopg2.extras

if len(sys.argv) != 3:
    print(__doc__)
    sys.exit(1)

name, slug = sys.argv[1], sys.argv[2].strip().lower()
url = os.environ.get('DATABASE_URL', '')
if url.startswith('postgres://'):
    url = url.replace('postgres://', 'postgresql://', 1)
if not url:
    sys.exit('DATABASE_URL 未设置')

base = os.environ.get('BASE_URL', 'https://hastridbooking.com').rstrip('/')
conn = psycopg2.connect(url)
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

cur.execute('SELECT * FROM schools WHERE slug=%s', (slug,))
row = cur.fetchone()
if row:
    print(f'学院已存在，直接给你链接：{row["name"]}')
else:
    cur.execute(
        'INSERT INTO schools (name, slug, token) VALUES (%s,%s,%s) RETURNING *',
        (name, slug, uuid.uuid4().hex)
    )
    row = cur.fetchone()
    conn.commit()
    print(f'已创建：{row["name"]}')

print(f'\n毕业生开通链接（给学院发给学生）：\n  {base}/register?school={row["slug"]}')
print(f'\n学院看板链接（只给校长，别外传）：\n  {base}/school/{row["token"]}\n')
conn.close()

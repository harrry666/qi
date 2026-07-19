#!/usr/bin/env python3
"""开一个合作院校，打印它的专属开通链接和看板链接。

slug 用在毕业生注册链接里，token 是学院看板的只读凭证（自动生成，别外传）。
重复跑同一个 slug 不会重建，只会把已有的链接再打印一遍。

默认只预览，不建校。加 --apply 才写库（防止学院名字打错直接建出来）。

用法：
    python3 scripts/add_school.py "VR Professional Beauty Academy" vr-professional
    python3 scripts/add_school.py "VR Professional Beauty Academy" vr-professional --apply
    DATABASE_URL='<生产库>' python3 scripts/add_school.py "学院名" slug --apply
"""
import os
import sys
import uuid
import argparse
import psycopg2
import psycopg2.extras


def main():
    ap = argparse.ArgumentParser(description='开一个合作院校（默认只预览）')
    ap.add_argument('name', help='学院全名，会显示在看板上，别打错')
    ap.add_argument('slug', help='用在毕业生注册链接里的短标识')
    ap.add_argument('--apply', action='store_true', help='真正建校，不加就只预览')
    args = ap.parse_args()

    name, slug = args.name, args.slug.strip().lower()
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
    elif not args.apply:
        print('将要创建的学院（请仔细核对名字和 slug）：')
        print(f'  名称：{name}')
        print(f'  slug：{slug}')
        print(f'\n毕业生开通链接会是：\n  {base}/register?school={slug}')
        print('\n以上只是预览，未写库。确认无误后加 --apply 真正创建。')
        conn.close()
        return
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


if __name__ == '__main__':
    main()

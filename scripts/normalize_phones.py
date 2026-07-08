#!/usr/bin/env python3
"""把 customers + appointments 里的手机号归一化到纯10位（M2 历史数据迁移）。

默认只读预览，不改任何数据。加 --apply 才会写库。
连接串优先级：--db 参数 > DATABASE_PUBLIC_URL 环境变量 > DATABASE_URL 环境变量。
生产库要用 Railway Postgres 的公网串（proxy.rlwy.net），内网 .internal 本地连不上。

用法：
  # 只读预览生产库
  python3 scripts/normalize_phones.py --db 'postgresql://...proxy.rlwy.net:PORT/railway'
  # 确认无误后执行
  python3 scripts/normalize_phones.py --db '...' --apply
"""
import os
import re
import sys
import argparse
from collections import defaultdict
import psycopg2
import psycopg2.extras


def normalize_phone(raw):
    """与 db.normalize_phone 完全一致：去符号、11位带1去国码、取后10位。"""
    digits = re.sub(r'\D', '', raw or '')
    if len(digits) == 11 and digits.startswith('1'):
        digits = digits[1:]
    return digits[-10:] if len(digits) >= 10 else digits


def get_url(args):
    url = args.db or os.environ.get('DATABASE_PUBLIC_URL') or os.environ.get('DATABASE_URL') or ''
    if url.startswith('postgres://'):
        url = url.replace('postgres://', 'postgresql://', 1)
    return url


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', help='数据库连接串（生产用 Railway 公网串）')
    ap.add_argument('--apply', action='store_true', help='真正写库；不加则只读预览')
    args = ap.parse_args()

    url = get_url(args)
    if not url:
        print('❌ 没有数据库连接串。用 --db 传，或设 DATABASE_PUBLIC_URL / DATABASE_URL')
        sys.exit(1)

    conn = psycopg2.connect(url)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute('SELECT id, business_id, phone, name FROM customers ORDER BY business_id, id')
    custs = cur.fetchall()
    changes = [(c, normalize_phone(c['phone'])) for c in custs if normalize_phone(c['phone']) != (c['phone'] or '')]

    # 归一后同店撞号：会顶到 UNIQUE(business_id, phone)，必须人工合并，不能盲改
    bmap = defaultdict(lambda: defaultdict(list))
    for c in custs:
        bmap[c['business_id']][normalize_phone(c['phone'])].append(c)
    collisions = [(biz, n, rows) for biz, d in bmap.items() for n, rows in d.items() if n and len(rows) > 1]
    collision_ids = {c['id'] for _, _, rows in collisions for c in rows}

    cur.execute('SELECT id, phone FROM appointments')
    apts = cur.fetchall()
    apt_changes = [a for a in apts if normalize_phone(a['phone']) != (a['phone'] or '')]

    print('==== 执行模式（会写库）====' if args.apply else '==== 只读预览（不改任何数据）====')
    print(f'\ncustomers 总数 {len(custs)}，需归一化 {len(changes)} 条：')
    for c, n in changes[:40]:
        print(f"  #{c['id']} biz{c['business_id']} {c['name'] or ''}: {c['phone']!r} -> {n!r}")
    if len(changes) > 40:
        print(f'  ...还有 {len(changes) - 40} 条')

    print(f'\n⚠️ 归一后同店撞号（需人工合并，脚本不会动这些）：{len(collisions)} 组')
    for biz, n, rows in collisions:
        who = ', '.join(f"#{r['id']}({r['phone']!r},{r['name'] or ''})" for r in rows)
        print(f"  biz{biz} 归一为 {n}: {who}")

    print(f'\nappointments 总数 {len(apts)}，phone 需归一化 {len(apt_changes)} 条')

    if not args.apply:
        print('\n这是预览。确认无误后加 --apply 才会真正写库（撞号的那几组不会动）。')
        conn.close()
        return

    # ── 执行 ──
    to_update = [(c, n) for c, n in changes if c['id'] not in collision_ids]
    for c, n in to_update:
        cur.execute('UPDATE customers SET phone=%s WHERE id=%s', (n, c['id']))
    for a in apt_changes:
        cur.execute('UPDATE appointments SET phone=%s WHERE id=%s', (normalize_phone(a['phone']), a['id']))
    conn.commit()
    conn.close()
    print(f'\n✅ 已归一化 customers {len(to_update)} 条、appointments {len(apt_changes)} 条。')
    if collisions:
        print(f'⚠️ 跳过了 {len(collisions)} 组撞号客户，请人工合并后再单独处理。')


if __name__ == '__main__':
    main()

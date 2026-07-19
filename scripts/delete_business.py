#!/usr/bin/env python3
"""删除商家及其全部关联数据。

schema 里没有任何 FOREIGN KEY，删漏了不会报错，只会静默留下孤儿行。
所以这里显式列出**每一张**挂 business_id 的表，以及通过 staff_id / customer_id
间接归属商家的表，按依赖顺序删。

默认只预览（每张表会删多少行 + 匹配到哪些商家），加 --apply 才真删。
所有删除包在一个事务里，中途失败整体回滚。

安全规则：
  - 按名字匹配到多个商家 → 直接报错退出，要求用 --id 精确指定
  - subscription_status 是 active / comp 的商家 → 必须再加 --allow-paid 才允许删

用法：
  python3 scripts/delete_business.py "Qi testing"                 # 预览
  python3 scripts/delete_business.py "Qi testing" --apply         # 真删
  python3 scripts/delete_business.py --id 42 --apply
  python3 scripts/delete_business.py --id 7 --apply --allow-paid
连接串优先级：--db > DATABASE_PUBLIC_URL > DATABASE_URL
"""
import os
import sys
import argparse
import psycopg2
import psycopg2.extras

# (表名, WHERE 条件)，条件里的 %s 都是 business_id。顺序 = 实际删除顺序：
# 先删依赖 staff / customers 的子表，再删 staff / customers 本身，最后删 businesses。
TABLES = [
    ('staff_hours', 'staff_id IN (SELECT id FROM staff WHERE business_id=%s)'),
    ('staff_services', 'staff_id IN (SELECT id FROM staff WHERE business_id=%s)'),
    ('staff', 'business_id=%s'),
    ('customer_photos', 'customer_id IN (SELECT id FROM customers WHERE business_id=%s)'),
    ('balance_transactions', 'customer_id IN (SELECT id FROM customers WHERE business_id=%s)'),
    ('customers', 'business_id=%s'),
    ('time_blocks', 'business_id=%s'),
    ('appointments', 'business_id=%s'),
    ('business_hours', 'business_id=%s'),
    ('business_blackouts', 'business_id=%s'),
    ('services', 'business_id=%s'),
    ('password_reset_tokens', 'business_id=%s'),
    ('sms_usage', 'business_id=%s'),
    ('broadcast_requests', 'business_id=%s'),
    ('platform_feedback', 'business_id=%s'),
    ('businesses', 'id=%s'),
]

PROTECTED_STATUS = ('active', 'comp')


def get_url(args):
    url = args.db or os.environ.get('DATABASE_PUBLIC_URL') or os.environ.get('DATABASE_URL') or ''
    if url.startswith('postgres://'):
        url = url.replace('postgres://', 'postgresql://', 1)
    return url


def resolve(cur, names, ids):
    """把名字/id 解析成商家行。名字匹配到多个就报错退出。"""
    found = []
    for bid in ids:
        cur.execute('SELECT id, name, slug, subscription_status FROM businesses WHERE id=%s', (bid,))
        row = cur.fetchone()
        if not row:
            sys.exit(f'× 未找到商家 id={bid}')
        found.append(row)
    for name in names:
        cur.execute(
            'SELECT id, name, slug, subscription_status FROM businesses '
            'WHERE LOWER(name)=LOWER(%s) ORDER BY id', (name,)
        )
        rows = cur.fetchall()
        if not rows:
            sys.exit(f'× 未找到商家：{name}')
        if len(rows) > 1:
            print(f'× 名字「{name}」匹配到 {len(rows)} 个商家，拒绝执行。用 --id 精确指定：')
            for r in rows:
                print(f'    --id {r["id"]}   {r["name"]}（slug={r["slug"]}, '
                      f'status={r["subscription_status"]}）')
            sys.exit(1)
        found.append(rows[0])
    # 同一个商家被 --id 和名字重复指定时只保留一次
    uniq, seen = [], set()
    for r in found:
        if r['id'] not in seen:
            seen.add(r['id'])
            uniq.append(r)
    return uniq


def count_rows(cur, bid):
    counts = {}
    for table, where in TABLES:
        cur.execute(f'SELECT COUNT(*) AS n FROM {table} WHERE {where}', (bid,))
        counts[table] = cur.fetchone()['n']
    return counts


def main():
    ap = argparse.ArgumentParser(description='删除商家及其全部关联数据（默认只预览）')
    ap.add_argument('names', nargs='*', help='商家名（不区分大小写，重名会拒绝执行）')
    ap.add_argument('--id', type=int, action='append', default=[], dest='ids',
                    help='按 business id 精确指定，可重复')
    ap.add_argument('--db', help='数据库连接串')
    ap.add_argument('--apply', action='store_true', help='真正删除，不加就只预览')
    ap.add_argument('--allow-paid', action='store_true',
                    help='允许删除 subscription_status 为 active / comp 的商家')
    args = ap.parse_args()

    if not args.names and not args.ids:
        ap.print_help()
        sys.exit(1)

    url = get_url(args)
    if not url:
        sys.exit('没有数据库连接串：用 --db 或设 DATABASE_URL')

    conn = psycopg2.connect(url)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    targets = resolve(cur, args.names, args.ids)

    protected = [b for b in targets if (b['subscription_status'] or '') in PROTECTED_STATUS]
    if protected and not args.allow_paid:
        print('× 以下商家是付费(active)或赠送(comp)会员，默认不允许删除。')
        print('  确认无误后加 --allow-paid：')
        for b in protected:
            print(f'    #{b["id"]} {b["name"]}（status={b["subscription_status"]}）')
        conn.close()
        sys.exit(1)

    grand = 0
    for b in targets:
        counts = count_rows(cur, b['id'])
        total = sum(counts.values())
        grand += total
        print(f'\n商家 #{b["id"]} {b["name"]}（slug={b["slug"]}, '
              f'status={b["subscription_status"]}）将删除 {total} 行：')
        for table, _ in TABLES:
            flag = '' if counts[table] else '   (空)'
            print(f'  {table:<24} {counts[table]:>6}{flag}')

    if not args.apply:
        print(f'\n以上只是预览，共 {grand} 行，未改动任何数据。确认无误后加 --apply 真正删除。')
        conn.close()
        return

    try:
        for b in targets:
            for table, where in TABLES:
                cur.execute(f'DELETE FROM {table} WHERE {where}', (b['id'],))
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        sys.exit(f'× 删除失败，已整体回滚，没有任何数据被改动：{e}')

    for b in targets:
        print(f'\n✓ 已删除商家 #{b["id"]} {b["name"]} 及全部关联数据')
    conn.close()
    print(f'\n完成，共删除 {grand} 行。')


if __name__ == '__main__':
    main()

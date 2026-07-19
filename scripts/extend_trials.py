#!/usr/bin/env python3
"""给现有商家补 6 个月免费试用期（2026-07-18 定价改版的补丁）。

TRIAL_DAYS 从 30 改成 180 只对新注册的店生效，改之前注册的还是 30 天到期。
这个脚本把他们补齐。

规则：
  - 除排除名单外，**所有**商家的试用期都延到「今天 + 180 天」
    从今天算而不是从注册日算：注册超过半年的老店按注册日算下来是过去时间，等于没给
  - 只延后不提前。本来到期日就更晚的（比如赠送会员）保持原样
  - 状态是 canceled / none 的会一起改回 trialing，否则光改到期日不恢复权限
  - **默认排除 comp（赠送会员，Chris）和 active（正在付钱的）**，用 --include-comp 可强行包含

默认只读预览，不改任何数据。加 --apply 才会写库。
连接串优先级：--db 参数 > DATABASE_PUBLIC_URL 环境变量 > DATABASE_URL 环境变量。
生产库要用 Railway Postgres 的公网串（proxy.rlwy.net），内网 .internal 本地连不上。

用法：
  python3 scripts/extend_trials.py --db 'postgresql://...proxy.rlwy.net:PORT/railway'
  python3 scripts/extend_trials.py --db '...' --apply
"""
import os
import sys
import argparse
import psycopg2
import psycopg2.extras

TRIAL_DAYS = 180
SKIP_STATUS = ('comp', 'active')


def get_url(args):
    url = args.db or os.environ.get('DATABASE_PUBLIC_URL') or os.environ.get('DATABASE_URL') or ''
    if url.startswith('postgres://'):
        url = url.replace('postgres://', 'postgresql://', 1)
    return url


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', help='数据库连接串（生产用 Railway 公网串）')
    ap.add_argument('--apply', action='store_true', help='真正写库，不加就只预览')
    ap.add_argument('--include-comp', action='store_true',
                    help='连赠送会员和付费商家一起改（默认不碰，Chris 是 comp）')
    args = ap.parse_args()

    url = get_url(args)
    if not url:
        sys.exit('没有数据库连接串：用 --db 或设 DATABASE_URL')

    skip = () if args.include_comp else SKIP_STATUS
    conn = psycopg2.connect(url)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute(
        "SELECT id, name, email, subscription_status AS st, trial_ends_at, "
        "NOW() + INTERVAL '%s days' AS new_end FROM businesses ORDER BY id" % TRIAL_DAYS
    )
    rows = cur.fetchall()

    skipped = [r for r in rows if r['st'] in skip]
    targets = [r for r in rows if r['st'] not in skip]
    changed = [r for r in targets if not r['trial_ends_at'] or r['new_end'] > r['trial_ends_at']]

    print(f'商家共 {len(rows)} 家，其中 {len(changed)} 家会延长到 '
          f"{changed[0]['new_end'].strftime('%Y-%m-%d') if changed else '-'}：\n")
    for r in changed:
        old = r['trial_ends_at'].strftime('%Y-%m-%d') if r['trial_ends_at'] else '无'
        print(f"  #{r['id']:<4} {r['name'][:22]:<24} [{r['st']:<9}] {old} → {r['new_end'].strftime('%Y-%m-%d')}")

    if skipped:
        print(f'\n跳过 {len(skipped)} 家（comp 赠送 / active 付费，Chris 应该在这里）：')
        for r in skipped:
            end = r['trial_ends_at'].strftime('%Y-%m-%d') if r['trial_ends_at'] else '无'
            print(f"  #{r['id']:<4} {r['name'][:22]:<24} [{r['st']:<9}] 到期 {end}")

    unchanged = len(targets) - len(changed)
    if unchanged:
        print(f'\n另有 {unchanged} 家不变（到期日本来就更晚）')

    if not changed:
        print('\n没有需要改的。')
    elif args.apply:
        cur.execute(
            "UPDATE businesses SET trial_ends_at = NOW() + INTERVAL '%s days', "
            "subscription_status = CASE WHEN subscription_status IN ('canceled','past_due','none') "
            "  OR subscription_status IS NULL THEN 'trialing' ELSE subscription_status END "
            "WHERE (trial_ends_at IS NULL OR NOW() + INTERVAL '%s days' > trial_ends_at) "
            "%s" % (TRIAL_DAYS, TRIAL_DAYS,
                    '' if args.include_comp else
                    "AND (subscription_status IS NULL OR subscription_status NOT IN %s)" % (SKIP_STATUS,))
        )
        conn.commit()
        print(f'\n✓ 已更新 {cur.rowcount} 家。')
    else:
        print('\n以上只是预览。确认无误后加 --apply 真正执行。')
    conn.close()


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""在 Stripe 建席位阶梯 price（2026-07-18 定价改版要用的）。

价格结构（volume 分层，按订阅里的 quantity = 在职员工数计费）：
    1 席        → $15.00
    2 席        → $20.00
    3 席        → $30.00
    4 席及以上  → $39.99（封顶，再招人也不涨）

封顶交给 Stripe 的 volume tier，代码里不夹 quantity。建完把打印出来的 price id
配成环境变量 STRIPE_SEAT_PRICE_ID，代码才会走席位计费；没配会自动退回旧的单席位价。

用法（测试环境）：
    python3 scripts/create_seat_price.py                  # 读 .env 里的 STRIPE_SECRET_KEY
生产环境（用你自己的 live key，别写进 .env）：
    STRIPE_SECRET_KEY='sk_live_...' python3 scripts/create_seat_price.py

重复跑会新建一个 price，Stripe 的 price 建了不能改价，只能停用后重建。
"""
import os
import sys
import stripe
from dotenv import load_dotenv

load_dotenv()

TIERS = [
    {'up_to': 1, 'flat_amount': 1500},
    {'up_to': 2, 'flat_amount': 2000},
    {'up_to': 3, 'flat_amount': 3000},
    {'up_to': 'inf', 'flat_amount': 3999},
]

stripe.api_key = os.environ.get('STRIPE_SECRET_KEY', '')
if not stripe.api_key:
    sys.exit('STRIPE_SECRET_KEY 未设置')

# 密钥有 sk_（标准）和 rk_（受限）两种，都可能是 live。
# 只认 sk_live 会漏掉 rk_live，导致在生产上不弹确认就直接建，所以按第二段判断。
_parts = stripe.api_key.split('_')
live = len(_parts) > 1 and _parts[1] == 'live'
restricted = stripe.api_key.startswith('rk_')
print(f"模式：{'生产 LIVE' if live else '测试 TEST'}"
      f"{'（受限密钥 rk_）' if restricted else ''}")
if live:
    ans = input('这会在生产 Stripe 建一个真实 price，继续？输入 yes 确认：')
    if ans.strip().lower() != 'yes':
        sys.exit('已取消')

try:
    products = stripe.Product.list(limit=100)
    product = next((p for p in products.data if p.name == 'Hastrid 全功能版'), None)
    if product:
        print(f'复用已有 product：{product.id}')
    else:
        product = stripe.Product.create(name='Hastrid 全功能版',
                                        description='在线预约系统，按在职员工数计费，封顶 $39.99/月')
        print(f'新建 product：{product.id}')

    price = stripe.Price.create(
        product=product.id,
        currency='usd',
        recurring={'interval': 'month'},
        billing_scheme='tiered',
        tiers_mode='volume',
        tiers=TIERS,
    )
except stripe.error.PermissionError as e:
    sys.exit(
        f'\n权限不够：{e}\n\n'
        '这个密钥没有建 Product / Price 的权限。两个办法：\n'
        '  1. 换用标准密钥 sk_live_（Stripe 后台 API keys 页的 Secret key 那一行）\n'
        '  2. 或者去 Stripe 后台把这个受限密钥的 Products 和 Prices 权限都改成 Write\n'
    )
print(f'\n✓ 已建席位阶梯 price：{price.id}\n')
print('核对一下分层：')
# stripe 对象不支持 .get()，属性访问，up_to 为 None 表示最高一档
for t in stripe.Price.retrieve(price.id, expand=['tiers']).tiers or []:
    upto = t.up_to or '∞'
    print(f"  ≤{str(upto):<4} → ${t.flat_amount / 100:.2f}")
print(f'\n把这个配进环境变量：\n  STRIPE_SEAT_PRICE_ID={price.id}\n')

"""席位定价回归测试。跑法: pytest tests/test_pricing.py

护的是 2026-07-18 的定价改版：1 席 $15，2 席起 $10/席，4 席及以上封顶 $39.99。
封顶那条最关键——改错了大店会被按人头收到天上去。
改 billing.py 的价格常量或 plan_for 前必跑。
"""
import pytest

# db 相关的 import 延后到函数内，理由同 test_time_blocks.py

SLUG = 'regtest-pricing'


def test_solo_price():
    from billing import plan_for
    p = plan_for(1)
    assert p['price'] == 15.00
    assert p['sms_included'] == 200
    assert not p['capped']


def test_no_staff_still_bills_one_seat():
    from billing import plan_for
    assert plan_for(0)['price'] == 15.00


def test_team_per_seat():
    from billing import plan_for
    assert plan_for(2)['price'] == 20.00
    assert plan_for(3)['price'] == 30.00
    assert plan_for(2)['sms_included'] == 600


def test_cap_holds():
    from billing import plan_for
    for seats in (4, 5, 10, 50):
        p = plan_for(seats)
        assert p['price'] == 39.99, f'{seats} 席破了封顶'
        assert p['capped']


class _Biz:
    """冒充 models.Business，sub_state 只用 getattr 取这三个字段。"""
    def __init__(self, status, sub_id=None, ends=None):
        self.subscription_status = status
        self.stripe_subscription_id = sub_id
        self.trial_ends_at = ends


def test_canceled_can_resubscribe():
    """取消订阅后账单页必须重新出现订阅按钮。

    模板是 {% elif sub.subscribed %} 挡在订阅按钮前面的，subscribed 一旦恒为真，
    商家会被 dashboard 的硬锁困在账单页且没有任何自助复购入口。
    """
    from billing import sub_state
    s = sub_state(_Biz('canceled', sub_id='sub_dead'))
    assert not s['subscribed'], 'canceled 还算已订阅，订阅按钮就永远出不来'
    assert not s['has_access']


def test_subscribed_during_trial_still_true():
    from billing import sub_state
    from datetime import datetime, timezone, timedelta
    ends = datetime.now(timezone.utc) + timedelta(days=30)
    s = sub_state(_Biz('trialing', sub_id='sub_live', ends=ends))
    assert s['subscribed'], '试用期内已绑卡的不该再显示订阅按钮'


def test_never_subscribed_is_not_subscribed():
    from billing import sub_state
    assert not sub_state(_Biz('none'))['subscribed']


def _future():
    from datetime import datetime, timezone, timedelta
    return datetime.now(timezone.utc) + timedelta(days=30)


@pytest.mark.parametrize('status, sub_id, ends, blocked', [
    ('none',     None,       None,      False),  # 从没订阅过 -> 放行
    ('trialing', None,       _future(), False),  # 试用中未绑卡 -> 放行
    ('trialing', 'sub_live', _future(), True),   # 试用中已绑卡 -> 拦，否则第二条订阅
    ('active',   'sub_live', None,      True),   # 正常订阅 -> 拦
    ('past_due', 'sub_live', None,      True),   # 欠费但订阅还在 -> 拦，该走补款不是重开
    ('canceled', 'sub_dead', None,      False),  # 已取消 -> 必须能复购
])
def test_checkout_gate_matrix(status, sub_id, ends, blocked):
    """stripe_billing.checkout() 的服务端门禁就是 sub_state()['subscribed']。

    绕过前端直接 POST /dashboard/billing/checkout 会给同一个 customer 建第二条订阅，
    两条都真实扣钱。这个矩阵护的是"该拦的拦住，canceled 的复购路径不能被堵死"。
    """
    from billing import sub_state
    assert sub_state(_Biz(status, sub_id=sub_id, ends=ends))['subscribed'] is blocked


@pytest.mark.db
def test_seat_count_counts_only_active_staff():
    from db import get_db
    from billing import seat_count, plan_of
    db = get_db()
    db.execute("DELETE FROM businesses WHERE slug=%s", (SLUG,))
    db.commit()
    db.execute(
        "INSERT INTO businesses (slug, name, email, password_hash, subscription_status) "
        "VALUES (%s,'RegTest Pricing','regtest-pricing@test.com','x','active')", (SLUG,)
    )
    biz_id = db.execute("SELECT id FROM businesses WHERE slug=%s", (SLUG,)).fetchone()['id']
    try:
        assert seat_count(biz_id) == 1, '没建员工时应按 1 席算'
        for name, active in [('A', 1), ('B', 1), ('C', 0)]:
            db.execute("INSERT INTO staff (business_id, name, is_active) VALUES (%s,%s,%s)",
                       (biz_id, name, active))
        db.commit()
        assert seat_count(biz_id) == 2, '离职员工不该计费'
        assert plan_of(biz_id)['price'] == 20.00
    finally:
        db.execute("DELETE FROM staff WHERE business_id=%s", (biz_id,))
        db.execute("DELETE FROM businesses WHERE id=%s", (biz_id,))
        db.commit()
        db.close()

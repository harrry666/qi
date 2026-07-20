"""时段锁定回归测试。跑法: pytest tests/test_time_blocks.py（连本地 qi_dev，用完自动清理测试商家）

护的是 2026-07-16 那个 bug：后台锁定的时段客户还能约进去。
改 booking.py 排档/锁定逻辑前必跑。
"""
from datetime import date, timedelta
import pytest

pytestmark = pytest.mark.db

# db / booking 的 import 必须延后到函数内：extensions.py 在 import 时就建连接池，
# 顶层 import 会让「连不上库自动跳过」和「拦住生产库」的守门在收集阶段就失效。

SLUG = 'regtest-time-blocks'
def _next_monday():
    """必须取未来的周一：generate_slots 会把「今天」已经过去的时段过滤掉，
    硬编码日期一旦撞上当天，上午的 slot 全没了，测试结果就跟锁定逻辑无关了。"""
    d = date.today() + timedelta(days=1)
    while d.weekday() != 0:
        d += timedelta(days=1)
    return d


DATE = _next_monday()


def make_business(with_staff_link):
    from db import get_db
    db = get_db()
    db.execute("DELETE FROM businesses WHERE slug=%s", (SLUG,))
    db.commit()
    db.execute(
        "INSERT INTO businesses (slug, name, email, password_hash, subscription_status) "
        "VALUES (%s,'RegTest','regtest@test.com','x','active')", (SLUG,)
    )
    biz_id = db.execute("SELECT id FROM businesses WHERE slug=%s", (SLUG,)).fetchone()['id']
    db.execute("INSERT INTO business_hours (business_id, weekday, open_time, close_time, is_closed) VALUES (%s,0,'09:00','18:00',0)", (biz_id,))
    db.execute("INSERT INTO services (business_id, name, duration_mins, is_active) VALUES (%s,'Test Service',30,1)", (biz_id,))
    service_id = db.execute("SELECT id FROM services WHERE business_id=%s", (biz_id,)).fetchone()['id']
    db.execute("INSERT INTO staff (business_id, name, is_active) VALUES (%s,'StaffA',1)", (biz_id,))
    staff_a = db.execute("SELECT id FROM staff WHERE business_id=%s", (biz_id,)).fetchone()['id']
    if with_staff_link:
        db.execute("INSERT INTO staff (business_id, name, is_active) VALUES (%s,'StaffB',1)", (biz_id,))
        staff_b = db.execute("SELECT id FROM staff WHERE business_id=%s AND name='StaffB'", (biz_id,)).fetchone()['id']
        db.execute("INSERT INTO staff_hours (staff_id, weekday, open_time, close_time, is_closed) VALUES (%s,0,'09:00','18:00',0)", (staff_a,))
        db.execute("INSERT INTO staff_hours (staff_id, weekday, open_time, close_time, is_closed) VALUES (%s,0,'09:00','18:00',0)", (staff_b,))
        db.execute("INSERT INTO staff_services (staff_id, service_id) VALUES (%s,%s)", (staff_a, service_id))
        db.execute("INSERT INTO staff_services (staff_id, service_id) VALUES (%s,%s)", (staff_b, service_id))
    else:
        staff_b = None
    db.commit()
    db.close()
    return biz_id, service_id, staff_a, staff_b


@pytest.fixture
def shop(request):
    """建测试商家，测试跑完无论成败都删掉。参数 True=员工关联了服务"""
    from db import get_db
    with_staff_link = getattr(request, 'param', False)
    ids = make_business(with_staff_link)
    yield ids
    db = get_db()
    db.execute("DELETE FROM businesses WHERE slug=%s", (SLUG,))
    db.commit()
    db.close()


def block(biz_id, staff_id, start, end):
    from db import get_db
    db = get_db()
    db.execute(
        "INSERT INTO time_blocks (business_id, staff_id, date, start_time, end_time, reason) VALUES (%s,%s,%s,%s,%s,'')",
        (biz_id, staff_id, DATE.strftime('%Y-%m-%d'), start, end)
    )
    db.commit()
    db.close()


@pytest.mark.parametrize('shop', [False], indirect=True)
def test_no_staff_linked_respects_staff_specific_block(shop):
    """无员工关联服务：锁定某个员工的时段，客户"不限员工"预约也不能约进去（2026-07-16 修的 bug）"""
    biz_id, service_id, staff_a, _ = shop
    block(biz_id, staff_a, '10:30', '11:00')
    from blueprints.booking import slots_for_service
    slots = slots_for_service(biz_id, DATE, 30, service_id)
    assert '10:30' not in slots, '员工专属锁定没生效，锁了还能约进去'


@pytest.mark.parametrize('shop', [True], indirect=True)
def test_multi_staff_union_still_works(shop):
    """多员工都关联服务：锁定其中一人，另一人还有空，该时段仍应可约（不能锁过头）"""
    biz_id, service_id, staff_a, _ = shop
    block(biz_id, staff_a, '10:30', '11:00')
    from blueprints.booking import slots_for_service
    slots = slots_for_service(biz_id, DATE, 30, service_id)
    assert '10:30' in slots, '锁一个员工把其他有空的员工也锁掉了'


@pytest.mark.parametrize('shop', [False], indirect=True)
def test_whole_store_block_always_blocks(shop):
    """整店锁定（staff_id 为空）：任何情况下都不可约"""
    biz_id, service_id, _, _ = shop
    block(biz_id, None, '10:30', '11:00')
    from blueprints.booking import slots_for_service
    slots = slots_for_service(biz_id, DATE, 30, service_id)
    assert '10:30' not in slots, '整店锁定没生效'


CLOSED_SLUG = 'regtest-store-closed'
@pytest.fixture
def closed_shop():
    """店铺当天休业（business_hours.is_closed=1），但员工个人排班这天是开的。
    设计 A：店铺休业是硬关闭，员工排班盖不过去，跑完删商家。"""
    from db import get_db
    db = get_db()
    db.execute("DELETE FROM businesses WHERE slug=%s", (CLOSED_SLUG,))
    db.execute(
        "INSERT INTO businesses (slug, name, email, password_hash, subscription_status) "
        "VALUES (%s,'RegTestClosed','regtestclosed@test.com','x','active')", (CLOSED_SLUG,)
    )
    biz_id = db.execute("SELECT id FROM businesses WHERE slug=%s", (CLOSED_SLUG,)).fetchone()['id']
    db.execute("INSERT INTO business_hours (business_id, weekday, open_time, close_time, is_closed) VALUES (%s,0,'09:00','18:00',1)", (biz_id,))
    db.execute("INSERT INTO services (business_id, name, duration_mins, is_active) VALUES (%s,'Test Service',30,1)", (biz_id,))
    service_id = db.execute("SELECT id FROM services WHERE business_id=%s", (biz_id,)).fetchone()['id']
    db.execute("INSERT INTO staff (business_id, name, is_active) VALUES (%s,'StaffA',1)", (biz_id,))
    staff_a = db.execute("SELECT id FROM staff WHERE business_id=%s", (biz_id,)).fetchone()['id']
    db.execute("INSERT INTO staff_hours (staff_id, weekday, open_time, close_time, is_closed) VALUES (%s,0,'09:00','18:00',0)", (staff_a,))
    db.execute("INSERT INTO staff_services (staff_id, service_id) VALUES (%s,%s)", (staff_a, service_id))
    db.commit()
    db.close()
    yield biz_id, service_id, staff_a
    db = get_db()
    db.execute("DELETE FROM businesses WHERE slug=%s", (CLOSED_SLUG,))
    db.commit()
    db.close()


def test_store_closed_shadows_staff_open(closed_shop):
    """店铺当天休业时，员工个人排班开着也不能约进去。想约只能走加班预约。"""
    biz_id, service_id, staff_a = closed_shop
    from blueprints.booking import slots_for_service
    assert slots_for_service(biz_id, DATE, 30, service_id) == [], '店铺休业没盖过员工排班（不限员工）'
    assert slots_for_service(biz_id, DATE, 30, service_id, staff_id=staff_a) == [], '店铺休业没盖过员工排班（指定员工）'

"""跑法: python3 tests/test_time_blocks.py（连本地 qi_dev，用完自动清理测试商家）"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import date
from db import get_db
from blueprints.booking import slots_for_service

SLUG = 'regtest-time-blocks'
DATE = date(2026, 7, 20)  # Monday

def setup(with_staff_link):
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

def teardown():
    db = get_db()
    db.execute("DELETE FROM businesses WHERE slug=%s", (SLUG,))
    db.commit()
    db.close()

def block(biz_id, staff_id, start, end):
    db = get_db()
    db.execute(
        "INSERT INTO time_blocks (business_id, staff_id, date, start_time, end_time, reason) VALUES (%s,%s,%s,%s,%s,'')",
        (biz_id, staff_id, DATE.strftime('%Y-%m-%d'), start, end)
    )
    db.commit()
    db.close()

def check(label, condition):
    status = 'PASS' if condition else 'FAIL'
    print(f'[{status}] {label}')
    return condition

def test_no_staff_linked_respects_staff_specific_block():
    """无员工关联服务：锁定某个员工的时段，客户"不限员工"预约也不能约进去（本次修复的 bug）"""
    biz_id, service_id, staff_a, _ = setup(with_staff_link=False)
    try:
        block(biz_id, staff_a, '10:30', '11:00')
        slots = slots_for_service(biz_id, DATE, 30, service_id)
        return check('无关联员工时，员工专属锁定生效', '10:30' not in slots)
    finally:
        teardown()

def test_multi_staff_union_still_works():
    """多员工都关联服务：锁定其中一人，另一人还有空，该时段仍应可约（不能锁过头）"""
    biz_id, service_id, staff_a, staff_b = setup(with_staff_link=True)
    try:
        block(biz_id, staff_a, '10:30', '11:00')
        slots = slots_for_service(biz_id, DATE, 30, service_id)
        return check('锁一人不影响其他有空员工', '10:30' in slots)
    finally:
        teardown()

def test_whole_store_block_always_blocks():
    """整店锁定（staff_id 为空）：任何情况下都不可约"""
    biz_id, service_id, _, _ = setup(with_staff_link=False)
    try:
        block(biz_id, None, '10:30', '11:00')
        slots = slots_for_service(biz_id, DATE, 30, service_id)
        return check('整店锁定生效', '10:30' not in slots)
    finally:
        teardown()

if __name__ == '__main__':
    results = [
        test_no_staff_linked_respects_staff_specific_block(),
        test_multi_staff_union_still_works(),
        test_whole_store_block_always_blocks(),
    ]
    if all(results):
        print('\n全部通过')
        sys.exit(0)
    else:
        print('\n有测试失败，锁定时段的逻辑可能又被破坏了')
        sys.exit(1)

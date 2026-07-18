"""时区回归测试。跑法: pytest tests/test_timezone.py（纯静态扫描，不连数据库）

appointment_dt 在库里存的是洛杉矶本地时间字符串，服务器（Railway）跑在 UTC。
任何裸 datetime.now() 都会拿到 UTC 时钟，夏令时期间快 7 小时，
导致「今日预约」「即将到来」「本月统计」「提醒窗口」全部错位。
一律用 datetime.now(_LA)。
"""
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGETS = ['app.py', 'blueprints/dashboard.py', 'blueprints/api.py', 'blueprints/booking.py']
BARE_NOW = re.compile(r'datetime\.now\(\s*\)')


def test_no_bare_now():
    bad = []
    for rel in TARGETS:
        for i, line in enumerate(open(os.path.join(ROOT, rel)), 1):
            if BARE_NOW.search(line):
                bad.append(f'{rel}:{i}: {line.strip()}')
    assert not bad, '发现裸 datetime.now()，应改成 datetime.now(_LA):\n' + '\n'.join(bad)


def test_la_is_defined():
    for rel in TARGETS:
        src = open(os.path.join(ROOT, rel)).read()
        if 'datetime.now(_LA)' in src:
            assert "_LA = ZoneInfo('America/Los_Angeles')" in src, f'{rel} 用了 _LA 但没定义'


def test_la_offset_sane():
    offset = round(datetime.now(ZoneInfo('America/Los_Angeles')).utcoffset().total_seconds() / 3600)
    assert offset in (-7, -8), f'洛杉矶偏移异常: {offset}'

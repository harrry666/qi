"""跑法: python3 tests/test_timezone.py（纯静态扫描，不连数据库）

appointment_dt 在库里存的是洛杉矶本地时间字符串，服务器（Railway）跑在 UTC。
任何裸 datetime.now() 都会拿到 UTC 时钟，夏令时期间快 7 小时，
导致「今日预约」「即将到来」「本月统计」「提醒窗口」全部错位。
一律用 datetime.now(_LA)。
"""
import sys, os, re
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

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

def test_now_la_matches_wall_clock():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    la = datetime.now(ZoneInfo('America/Los_Angeles'))
    offset = round(la.utcoffset().total_seconds() / 3600)
    assert offset in (-7, -8), f'洛杉矶偏移异常: {offset}'

if __name__ == '__main__':
    for name, fn in list(globals().items()):
        if name.startswith('test_'):
            fn()
            print(f'PASS {name}')
    print('全部通过')

import pytest
from blueprints.booking import (
    build_confirm_sms, build_reminder_sms, count_segments, short_address, wx_short_address
)

LINK = 'https://hastridbooking.com/c/aBcDeFgHiJk'
PHONE = '9097297920'


@pytest.mark.parametrize('raw,expected', [
    ('7128, Riley Dr, Fontana CA 92336', '7128, Riley Dr, Fontana'),
    ('9668 Foothill Blvd, Rancho Cucamonga, CA 91730', '9668 Foothill Blvd, Rancho Cucamonga'),
    ('9668 Foothill Blvd', '9668 Foothill Blvd'),
    ('N/A', ''), ('NA', ''), ('none', ''), ('无', ''), ('', ''), (None, ''),
])
def test_short_address(raw, expected):
    assert short_address(raw) == expected


def test_confirm_has_address_and_no_phone():
    msg = build_confirm_sms('Chris Hair Studio', '男士剪发', '2026-07-31 14:00',
                            '7128, Riley Dr, Fontana CA 92336', LINK)
    assert '7128, Riley Dr, Fontana' in msg
    assert LINK in msg
    assert PHONE not in msg


def test_reminder_says_tomorrow_and_has_address():
    msg = build_reminder_sms('Chris Hair Studio', '男士剪发', '2026-07-31 14:00',
                             '7128, Riley Dr, Fontana CA 92336', LINK, PHONE)
    assert '明天' in msg
    assert '7128, Riley Dr, Fontana' in msg
    assert PHONE in msg
    assert count_segments(msg) == 2


def test_reminder_drops_phone_before_address():
    """额度不够时电话先走，地址是客人当天真正要用的"""
    msg = build_reminder_sms('超级无敌长名字美容美发养生连锁旗舰店', '深层清洁补水面部护理套餐',
                             '2026-07-31 14:00', '12345 East Foothill Blvd, Rancho Cucamonga', LINK, PHONE)
    assert 'Foothill' in msg
    assert PHONE not in msg


def test_placeholder_address_produces_no_address_line():
    msg = build_confirm_sms("Harry's studio", 'Haircut', '2026-06-11 10:30', 'N/A', LINK)
    assert '地址' not in msg
    assert 'N/A' not in msg


@pytest.mark.parametrize('lang', ['zh', 'en'])
@pytest.mark.parametrize('biz,svc,addr', [
    ('嗨皮妞美甲', '全套美甲', '9668 Foothill Blvd, Rancho Cucamonga, CA 91730'),
    ('Chris Hair Studio', '男士剪发+洗吹造型', '7128, Riley Dr, Fontana CA 92336'),
    ('超级无敌长名字美容美发养生连锁旗舰店', '深层清洁补水面部护理套餐',
     '12345 East Foothill Boulevard Suite 1200, Rancho Cucamonga, CA 91730'),
    ('嗨皮妞美甲', '全套美甲', ''),
])
def test_never_exceeds_two_segments(lang, biz, svc, addr):
    """短信按段计费，2 段是成本红线。任何店名/服务名/地址长度都得靠降级压住"""
    assert count_segments(build_confirm_sms(biz, svc, '2026-07-31 14:00', addr, LINK, lang)) <= 2
    assert count_segments(build_reminder_sms(biz, svc, '2026-07-31 14:00', addr, LINK, PHONE, lang)) <= 2


@pytest.mark.parametrize('raw,expected', [
    ('7128, Riley Dr, Fontana CA 92336', '7128, Riley Dr'),
    ('9668 Foothill Blvd, Rancho Cucamonga, CA 91730', '9668 Foothill Blvd'),
    ('12345 East Foothill Boulevard Suite 1200, Rancho Cucamonga, CA 91730', '12345 East Foothill…'),
    ('9668 Foothill Blvd', '9668 Foothill Blvd'),
    ('N/A', ''), ('', ''), (None, ''),
])
def test_wx_short_address(raw, expected):
    """微信 thing 字段超 20 字会被拒发，宁可砍城市也不能让门牌号被截半截"""
    assert wx_short_address(raw) == expected
    assert len(wx_short_address(raw)) <= 20


def test_bad_date_does_not_crash():
    msg = build_confirm_sms('店', '服务', 'not-a-date', '', LINK)
    assert 'not-a-date' in msg

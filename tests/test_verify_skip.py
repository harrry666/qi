import pytest
from datetime import datetime, timedelta

pytestmark = pytest.mark.db

# db / booking 的 import 必须延后到函数内，理由同 test_time_blocks.py

PHONE = '9095550001'


@pytest.fixture(autouse=True)
def _clean():
    from db import get_db
    db = get_db()
    db.execute('DELETE FROM verified_phones WHERE phone=%s', (PHONE,))
    db.commit()
    db.close()
    yield
    db = get_db()
    db.execute('DELETE FROM verified_phones WHERE phone=%s', (PHONE,))
    db.commit()
    db.close()


def _set_verified_at(when):
    from db import get_db
    db = get_db()
    db.execute(
        'INSERT INTO verified_phones (phone, verified_at) VALUES (%s, %s) '
        'ON CONFLICT (phone) DO UPDATE SET verified_at = EXCLUDED.verified_at',
        (PHONE, when)
    )
    db.commit()
    db.close()


def test_unknown_phone_is_not_verified():
    from blueprints.booking import phone_verified_recently
    assert phone_verified_recently(PHONE) is False
    assert phone_verified_recently('') is False


def test_mark_then_recently_verified():
    from blueprints.booking import phone_verified_recently, mark_phone_verified
    mark_phone_verified(PHONE)
    assert phone_verified_recently(PHONE) is True


def test_inside_window_skips():
    from blueprints.booking import phone_verified_recently, VERIFY_SKIP_DAYS, _LA
    _set_verified_at(datetime.now(_LA) - timedelta(days=VERIFY_SKIP_DAYS - 1))
    assert phone_verified_recently(PHONE) is True


def test_expired_window_requires_code_again():
    """过了窗口必须重新验证，否则等于永久关掉手机验证"""
    from blueprints.booking import phone_verified_recently, VERIFY_SKIP_DAYS, _LA
    _set_verified_at(datetime.now(_LA) - timedelta(days=VERIFY_SKIP_DAYS + 1))
    assert phone_verified_recently(PHONE) is False


def test_mark_twice_refreshes_window():
    from blueprints.booking import phone_verified_recently, mark_phone_verified, VERIFY_SKIP_DAYS, _LA
    _set_verified_at(datetime.now(_LA) - timedelta(days=VERIFY_SKIP_DAYS + 1))
    mark_phone_verified(PHONE)
    assert phone_verified_recently(PHONE) is True


def test_check_verify_code_passes_without_code_for_known_phone(monkeypatch):
    """老客免验证的主路径：没有验证码也放行，且完全不碰 Twilio"""
    import blueprints.booking as bk
    monkeypatch.setattr(bk, 'TWILIO_VERIFY_SID', 'VAtest')
    bk.mark_phone_verified(PHONE)
    assert bk.check_verify_code(PHONE, '') == (True, None)


def test_check_verify_code_rejects_empty_code_for_new_phone(monkeypatch):
    import blueprints.booking as bk
    monkeypatch.setattr(bk, 'TWILIO_VERIFY_SID', 'VAtest')
    ok, err = bk.check_verify_code(PHONE, '')
    assert ok is False and err

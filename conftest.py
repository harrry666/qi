"""pytest 共享配置。

两件事：
1. 把项目根加进 sys.path，测试里能直接 `from db import get_db`。
2. 给 `@pytest.mark.db` 标记的测试做守门——本地库连不上就跳过，
   DATABASE_URL 指向非本地库就直接失败（这些测试会写数据，绝不能碰生产）。
"""
import os
import sys
import pytest
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

LOCAL_HOSTS = {'localhost', '127.0.0.1', '', None}


def _db_url():
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, '.env'))
    url = os.environ.get('DATABASE_URL', '')
    return url.replace('postgres://', 'postgresql://', 1) if url.startswith('postgres://') else url


@pytest.fixture(scope='session')
def db_ready():
    url = _db_url()
    if not url:
        pytest.skip('DATABASE_URL 未设置，跳过连库测试')
    host = urlparse(url).hostname
    if host not in LOCAL_HOSTS:
        pytest.fail(
            f'DATABASE_URL 指向非本地库 ({host})。连库测试会写入并删除数据，'
            f'只能对本地 qi_dev 跑。'
        )
    import psycopg2
    try:
        psycopg2.connect(url).close()
    except Exception as e:
        pytest.skip(f'本地库连不上，跳过连库测试：{e}')


@pytest.fixture(autouse=True)
def _guard_db_marker(request):
    if request.node.get_closest_marker('db'):
        request.getfixturevalue('db_ready')

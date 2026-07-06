from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from psycopg2 import pool
import os

csrf = CSRFProtect()

_db_url = os.environ.get('DATABASE_URL', '')
if _db_url.startswith('postgres://'):
    _db_url = _db_url.replace('postgres://', 'postgresql://', 1)
db_pool = pool.ThreadedConnectionPool(1, 8, _db_url) if _db_url else None

_redis_url = os.environ.get('REDIS_URL') or os.environ.get('REDISCLOUD_URL')
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[],
    storage_uri=_redis_url if _redis_url else 'memory://',
)

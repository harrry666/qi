from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import os

csrf = CSRFProtect()

_redis_url = os.environ.get('REDIS_URL') or os.environ.get('REDISCLOUD_URL')
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[],
    storage_uri=_redis_url if _redis_url else 'memory://',
)

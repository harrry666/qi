import os
import sys
import json
import time
import threading
import urllib.parse
import urllib.request

WX_APPID = os.environ.get('WX_APPID', '')
WX_APPSECRET = os.environ.get('WX_APPSECRET', '')
WX_TEMPLATE_BOOKING = os.environ.get('WX_TEMPLATE_BOOKING', 'YUxZ8RpPrwZBczvbAC9JkM_u1hH3cSwrNjnvxVhc31c')

_token_cache = {'value': '', 'expires_at': 0}
_token_lock = threading.Lock()


def wx_configured():
    return bool(WX_APPID and WX_APPSECRET)


def _get_json(url):
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.loads(resp.read().decode('utf-8'))


def _post_json(url, payload):
    data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode('utf-8'))


def jscode2session(code):
    if not wx_configured():
        return None
    params = urllib.parse.urlencode({
        'appid': WX_APPID, 'secret': WX_APPSECRET,
        'js_code': code, 'grant_type': 'authorization_code',
    })
    try:
        res = _get_json(f'https://api.weixin.qq.com/sns/jscode2session?{params}')
        return res.get('openid')
    except Exception as e:
        print(f'[WX] jscode2session FAILED: {e}', flush=True, file=sys.stderr)
        return None


def get_access_token():
    if not wx_configured():
        return None
    with _token_lock:
        now = time.time()
        if _token_cache['value'] and _token_cache['expires_at'] > now:
            return _token_cache['value']
        params = urllib.parse.urlencode({
            'grant_type': 'client_credential', 'appid': WX_APPID, 'secret': WX_APPSECRET,
        })
        try:
            res = _get_json(f'https://api.weixin.qq.com/cgi-bin/token?{params}')
            token = res.get('access_token')
            if token:
                _token_cache['value'] = token
                _token_cache['expires_at'] = now + 7000
                return token
            print(f'[WX] get_access_token bad response: {res}', flush=True, file=sys.stderr)
        except Exception as e:
            print(f'[WX] get_access_token FAILED: {e}', flush=True, file=sys.stderr)
        return None


def send_subscribe_message(openid, data, page=''):
    if not wx_configured() or not WX_TEMPLATE_BOOKING:
        print('[WX] subscribe skip: not configured', flush=True, file=sys.stderr)
        return False
    token = get_access_token()
    if not token:
        return False
    payload = {'touser': openid, 'template_id': WX_TEMPLATE_BOOKING, 'data': data}
    if page:
        payload['page'] = page
    try:
        res = _post_json(
            f'https://api.weixin.qq.com/cgi-bin/message/subscribe/send?access_token={token}',
            payload
        )
        if res.get('errcode') == 0:
            print(f'[WX] subscribe sent to {openid}', flush=True, file=sys.stderr)
            return True
        print(f'[WX] subscribe FAILED {openid}: {res}', flush=True, file=sys.stderr)
        return False
    except Exception as e:
        print(f'[WX] subscribe FAILED {openid}: {e}', flush=True, file=sys.stderr)
        return False

"""把 TWILIO_FROM 号码的入站短信 webhook 指到 /sms/incoming。

在有 Twilio 凭据的环境里跑（生产在 Railway）：
    railway run python scripts/set_twilio_webhook.py
或本地临时导出凭据后直接：
    TWILIO_SID=xx TWILIO_TOKEN=xx TWILIO_FROM=+1xxx BASE_URL=https://hastridbooking.com \
        python scripts/set_twilio_webhook.py

幂等：已经指对了就不重复写。加 --dry-run 只看不改。
"""
import os
import sys
from twilio.rest import Client

sid = os.environ.get('TWILIO_SID', '')
token = os.environ.get('TWILIO_TOKEN', '')
from_number = os.environ.get('TWILIO_FROM', '')
base_url = os.environ.get('BASE_URL', '').rstrip('/')
dry_run = '--dry-run' in sys.argv

if not all([sid, token, from_number, base_url]):
    sys.exit('缺少环境变量：需要 TWILIO_SID / TWILIO_TOKEN / TWILIO_FROM / BASE_URL')

target = f'{base_url}/sms/incoming'
client = Client(sid, token)

nums = client.incoming_phone_numbers.list(phone_number=from_number)
if not nums:
    sys.exit(f'在该 Twilio 账户下没找到号码 {from_number}')

num = nums[0]
print(f'号码：{num.phone_number}  (SID {num.sid})')
print(f'当前 sms_url：{num.sms_url or "(空)"}  method={num.sms_method}')
print(f'目标 sms_url：{target}  method=POST')

if num.sms_url == target and num.sms_method == 'POST':
    print('已经配好，无需改动。')
    sys.exit(0)
if dry_run:
    print('[dry-run] 未修改。去掉 --dry-run 才会真正写入。')
    sys.exit(0)

num.update(sms_url=target, sms_method='POST')
print('✅ 已更新入站 webhook。客人回复「取消」现在会进入 /sms/incoming。')

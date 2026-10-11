# -*- coding: utf-8 -*-
import sys
sys.stdout.reconfigure(encoding='utf-8')
from curl_cffi import requests
import re

r = requests.get('https://www.runninghub.cn', impersonate='chrome120', timeout=15)
clean = re.sub(r'<[^>]+>', ' ', r.text)
for line in clean.splitlines():
    line = line.strip()
    if any(k in line for k in ['RH', '会员', '小时', '邀请', '免费', '限时', '币', '积分']):
        print(line)

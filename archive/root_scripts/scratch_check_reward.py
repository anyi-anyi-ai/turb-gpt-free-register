# -*- coding: utf-8 -*-
from curl_cffi import requests
import re

r = requests.get('https://www.runninghub.ai', impersonate='chrome120', timeout=20)
print('Status:', r.status_code)
scripts = re.findall(r'src=["\']([^"\']+\.js)["\']', r.text)
print('Scripts count:', len(scripts))

for s in scripts:
    if not s.startswith('http'):
        s_url = 'https://www.runninghub.ai' + (s if s.startswith('/') else '/' + s)
    else:
        s_url = s
    try:
        resp = requests.get(s_url, impersonate='chrome120', timeout=20)
        js = resp.text
        for kw in ['checkin', 'sign_in', 'daily', 'coin', 'points', 'reward', 'checkIn', 'signIn']:
            matches = [m.start() for m in re.finditer(kw, js, re.IGNORECASE)]
            if len(matches) > 3:
                print(f'{s_url} -> {kw}: {len(matches)}')
                for idx in matches[:2]:
                    snippet = js[max(0, idx-40):min(len(js), idx+60)].replace('\n', ' ')
                    print(f'   [{kw}]: {snippet}')
    except Exception as e:
        pass

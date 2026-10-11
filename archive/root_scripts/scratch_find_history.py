# -*- coding: utf-8 -*-
import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
import requests, json, re

with open('data/rh_accounts/CatherineAndrews17889LE@outlook.com.json', 'r', encoding='utf-8') as f:
    acc = json.load(f)

p = acc.get('proxy')
proxies = {'http': p, 'https': p} if p else None
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}

r = requests.get('https://www.runninghub.ai/ai-detail/2102677348907102210', headers=headers, proxies=proxies, timeout=20)
print('Page status:', r.status_code)
scripts = re.findall(r'src="(/_nuxt/[^"]+\.js)"', r.text)
print('Found scripts:', len(scripts))

for s in scripts:
    url = f'https://www.runninghub.ai{s}'
    try:
        res = requests.get(url, headers=headers, proxies=proxies, timeout=15)
        js = res.text
        for fn in ['createWebAppApi', 'outputTaskHistoryRes', 'outputHistoryRes']:
            if fn in js:
                pos = js.find(fn)
                print(f'Match in {s} for {fn}:', js[max(0, pos-80):min(len(js), pos+150)].replace('\n', ' '))
    except Exception as e:
        print('Err on', s, e)

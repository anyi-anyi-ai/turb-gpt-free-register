# -*- coding: utf-8 -*-
from curl_cffi import requests
import json

with open('data/rh_accounts/CatherineAndrews17889LE@outlook.com.json', 'r', encoding='utf-8') as f:
    acc = json.load(f)

token = acc['token']
proxy = acc['proxy']
proxies = {'http': proxy, 'https': proxy} if proxy else None
headers = {
    'Authorization': f'Bearer {token}',
    'Accept': 'application/json, text/plain, */*',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Origin': 'https://www.runninghub.ai',
    'Referer': 'https://www.runninghub.ai/',
    'Content-Type': 'application/json'
}

for ep, body in [
    ('/api/search/workflow', {'page': 1, 'size': 10, 'keyword': 'FLUX'}),
    ('/api/search/workflow', {'page': 1, 'size': 10, 'keyword': ''}),
    ('/api/portal/template/list', {'page': 1, 'size': 10}),
    ('/api/fast/creation/templates', {}),
]:
    try:
        r = requests.post(f"https://www.runninghub.ai{ep}", headers=headers, json=body, proxies=proxies, timeout=15)
        print(f"POST {ep} status={r.status_code}")
        print("  resp:", r.text[:300])
    except Exception as e:
        print(f"POST {ep} err: {e}")

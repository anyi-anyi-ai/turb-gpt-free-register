# -*- coding: utf-8 -*-
from curl_cffi import requests
import json, re

with open('data/rh_accounts/CatherineAndrews17889LE@outlook.com.json', 'r', encoding='utf-8') as f:
    acc = json.load(f)

token = acc['token']
headers = {
    'Authorization': f'Bearer {token}',
    'Accept': 'application/json, text/plain, */*',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Origin': 'https://www.runninghub.ai',
    'Referer': 'https://www.runninghub.ai/',
    'Content-Type': 'application/json'
}

# Look for call-api endpoints
for ep in [
    '/api/call-api/list',
    '/api/openapi/list',
    '/api/model/api/list',
    '/api/api-detail/2031354034474311686',
    '/task/openapi/create',
]:
    try:
        r = requests.post(f"https://www.runninghub.ai{ep}", headers=headers, json={}, impersonate='chrome120', timeout=10)
        print(f"POST {ep}: status={r.status_code} -> {r.text[:200]}")
    except Exception as e:
        print(f"POST {ep} err:", e)

# Also fetch the call-api page html
r_page = requests.get("https://www.runninghub.ai/call-api", impersonate='chrome120', timeout=15)
print("call-api page status:", r_page.status_code)
# Find api IDs
api_ids = re.findall(r'api-detail/(\d+)', r_page.text)
print("Found api-detail IDs:", list(dict.fromkeys(api_ids)))

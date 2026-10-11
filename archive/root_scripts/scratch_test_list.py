# -*- coding: utf-8 -*-
from curl_cffi import requests
import json

with open('data/rh_accounts/CatherineAndrews17889LE@outlook.com.json', 'r', encoding='utf-8') as f:
    acc = json.load(f)

token = acc['token']
proxy = acc['proxy']

headers = {
    'Authorization': f'Bearer {token}',
    'Accept': 'application/json, text/plain, */*',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Origin': 'https://www.runninghub.ai',
    'Referer': 'https://www.runninghub.ai/',
    'Content-Type': 'application/json'
}

# 1. Fetch webapp list
try:
    r = requests.post("https://www.runninghub.ai/api/fast/creation/specific/webapp/list", headers=headers, json={}, impersonate='chrome120', timeout=15)
    print("List status:", r.status_code)
    data = r.json().get('data') or []
    print("Webapps returned:", len(data))
    for item in data[:5]:
        print(f"  ID: {item.get('webappId') or item.get('id')} | Name: {item.get('webappName') or item.get('title') or item.get('name')}")
except Exception as e:
    print("List err:", e)

# 2. Try explore / post list
for ep in ['/api/explore/v2/list', '/api/portal/home/data', '/api/workflow/public/list']:
    try:
        r2 = requests.post(f"https://www.runninghub.ai{ep}", headers=headers, json={'current': 1, 'size': 10}, impersonate='chrome120', timeout=15)
        print(f"{ep}: {r2.status_code}")
        if r2.status_code == 200:
            print("  ", r2.text[:200])
    except Exception as e:
        print(ep, "err:", e)

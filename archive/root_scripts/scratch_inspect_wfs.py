# -*- coding: utf-8 -*-
from curl_cffi import requests
import json

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

r = requests.post("https://www.runninghub.ai/api/fast/creation/specific/webapp/list", headers=headers, json={}, impersonate='chrome120', timeout=15)
data = r.json().get('data') or []

print(f"Total specific webapps: {len(data)}")
for i, item in enumerate(data[:15]):
    wid = item.get('webappId') or item.get('id')
    wname = item.get('webappName') or item.get('title') or item.get('name')
    cover = item.get('coverUrl')
    cat = item.get('categoryName') or item.get('type')
    cost = item.get('consumeCoins') or item.get('cost')
    print(f"[{i+1}] ID: {wid} | Name: {wname} | Category: {cat} | Coins: {cost}")

# Inspect detail of one text-to-image workflow
target_id = data[3].get('webappId') or data[3].get('id')
r_det = requests.post("https://www.runninghub.ai/api/webapp/detail", headers=headers, json={'webappId': target_id}, impersonate='chrome120', timeout=15)
print("\n=== Detail of", target_id, "===")
det_data = r_det.json().get('data') or {}
print(json.dumps(det_data, indent=2, ensure_ascii=False)[:1000])

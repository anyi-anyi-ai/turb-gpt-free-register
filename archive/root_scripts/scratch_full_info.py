# -*- coding: utf-8 -*-
from curl_cffi import requests
import json, base64

with open('data/rh_accounts/CatherineAndrews17889LE@outlook.com.json', 'r', encoding='utf-8') as f:
    acc = json.load(f)

token = acc['token']
payload = json.loads(base64.b64decode(token.split('.')[1] + '==').decode('utf-8'))
sub_user_id = payload.get('sub')

headers = {
    'Authorization': f'Bearer {token}',
    'Accept': 'application/json, text/plain, */*',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Origin': 'https://www.runninghub.ai',
    'Referer': 'https://www.runninghub.ai/',
    'Content-Type': 'application/json'
}

# 1. Full getUserInfo
r = requests.post("https://www.runninghub.ai/uc/getUserInfo", headers=headers, json={'userId': sub_user_id}, impersonate='chrome120', timeout=15)
print("=== getUserInfo ===")
print(json.dumps(r.json(), indent=2, ensure_ascii=False))

# 2. Check all invite endpoints
for ep in [
    '/uc/invite/myRebate/details',
    '/uc/registeredConfigUser',
    '/api/member/wallet-exchange/info',
    '/api/product/info',
    '/uc/team/member/invite-link',
]:
    try:
        res = requests.post(f"https://www.runninghub.ai{ep}", headers=headers, json={}, impersonate='chrome120', timeout=15)
        print(f"=== {ep} ===")
        print(res.text[:400])
    except Exception as e:
        print(ep, "err:", e)

# -*- coding: utf-8 -*-
from curl_cffi import requests
import json

with open('data/rh_accounts/CatherineAndrews17889LE@outlook.com.json', 'r', encoding='utf-8') as f:
    acc = json.load(f)

token = acc['token']
proxy = acc['proxy']
# extract sub from jwt
import base64
payload = json.loads(base64.b64decode(token.split('.')[1] + '==').decode('utf-8'))
sub_user_id = payload.get('sub')
print("Extracted sub_user_id:", sub_user_id)

headers = {
    'Authorization': f'Bearer {token}',
    'Accept': 'application/json, text/plain, */*',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Origin': 'https://www.runninghub.ai',
    'Referer': 'https://www.runninghub.ai/',
    'Content-Type': 'application/json'
}

endpoints_to_test = [
    ('/uc/invite/myRebate/details', {}),
    ('/uc/getUserInfo', {}),
    ('/uc/getUserInfo', {'userId': sub_user_id}),
    ('/uc/getUserInfo', {'userId': acc['email']}),
    ('/uc/virtualCoin/records', {'page': 1, 'size': 10}),
    ('/api/product/coinGainList', {'page': 1, 'size': 10}),
]

for ep, body in endpoints_to_test:
    url = f"https://www.runninghub.ai{ep}"
    try:
        r = requests.post(url, headers=headers, json=body, impersonate='chrome120', timeout=15)
        print(f"POST {ep} with body {body}: status={r.status_code}")
        print("  resp:", r.text[:300])
    except Exception as e:
        print(f"POST {ep} err: {e}")

    try:
        r2 = requests.get(url, headers=headers, impersonate='chrome120', timeout=15)
        print(f"GET {ep}: status={r2.status_code}")
        print("  resp:", r2.text[:300])
    except Exception as e:
        print(f"GET {ep} err: {e}")

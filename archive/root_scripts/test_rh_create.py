# -*- coding: utf-8 -*-
from curl_cffi import requests
import json, time

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

# 1. Fetch webapp detail of 2026847165020114946
wid = "2026847165020114946"
r_det = requests.post("https://www.runninghub.ai/api/webapp/detail", headers=headers, json={'webappId': wid}, proxies=proxies, timeout=15)
det = r_det.json().get('data') or {}
print("Webapp name:", det.get('name'))
inputs = det.get('inputNodes') or []
print(f"Input nodes count: {len(inputs)}")

# 2. Build payload for /task/webapp/create
# RunningHub webapp create format:
# { "webappId": wid, "inputs": [ { "nodeId": ..., "fieldName": ..., "fieldValue": ... } ] }
task_inputs = []
for inp in inputs:
    task_inputs.append({
        "nodeId": inp.get('nodeId'),
        "fieldName": inp.get('fieldName'),
        "fieldValue": inp.get('fieldValue') # keep default
    })

payload = {
    "webappId": wid,
    "inputs": task_inputs
}

print("Submitting payload:")
print(json.dumps(payload, indent=2, ensure_ascii=False))

r_create = requests.post("https://www.runninghub.ai/task/webapp/create", headers=headers, json=payload, proxies=proxies, timeout=20)
print("Create response status:", r_create.status_code)
print("Create response body:", r_create.text)

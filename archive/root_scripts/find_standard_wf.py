# -*- coding: utf-8 -*-
import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
import requests
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

# Fetch webapp list
r = requests.post("https://www.runninghub.ai/api/fast/creation/specific/webapp/list", headers=headers, json={}, proxies=proxies, timeout=15)
data = r.json().get('data') or []
print(f"Total specific webapps: {len(data)}", flush=True)

# Test a few different webapps to find one that runs on pure GPU RH coins
for item in data[4:15]:
    wid = item.get('webappId') or item.get('id')
    wname = item.get('webappName') or item.get('title') or item.get('name')
    r_det = requests.post("https://www.runninghub.ai/api/webapp/detail", headers=headers, json={'webappId': wid}, proxies=proxies, timeout=10)
    det = r_det.json().get('data') or {}
    inputs = det.get('inputNodes') or []
    
    node_names = [inp.get('nodeName') for inp in inputs]
    print(f"\nTesting [{wname}] (ID: {wid}) -> nodes: {node_names}", flush=True)

    task_inputs = []
    for inp in inputs:
        task_inputs.append({
            "nodeId": inp.get('nodeId'),
            "fieldName": inp.get('fieldName'),
            "fieldValue": inp.get('fieldValue')
        })

    payload = {"webappId": wid, "inputs": task_inputs}
    r_create = requests.post("https://www.runninghub.ai/task/webapp/create", headers=headers, json=payload, proxies=proxies, timeout=15)
    resp_data = r_create.json()
    code = resp_data.get('code')
    print(f"  Result code: {code} | msg: {str(resp_data.get('msg'))[:150]}", flush=True)
    if code == 0:
        print("  🎉🎉🎉 TASK CREATED SUCCESSFULLY! Task data:", resp_data.get('data'), flush=True)
        break

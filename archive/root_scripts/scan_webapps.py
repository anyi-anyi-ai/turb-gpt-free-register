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

r = requests.post("https://www.runninghub.ai/api/fast/creation/specific/webapp/list", headers=headers, json={}, proxies=proxies, timeout=15)
data = r.json().get('data') or []

print(f"Total specific webapps: {len(data)}", flush=True)

t2i_webapps = []
i2i_webapps = []

for i, item in enumerate(data[:30]):
    wid = item.get('webappId') or item.get('id')
    wname = item.get('webappName') or item.get('title') or item.get('name')
    try:
        r_det = requests.post("https://www.runninghub.ai/api/webapp/detail", headers=headers, json={'webappId': wid}, proxies=proxies, timeout=10)
        det = r_det.json().get('data') or {}
        inputs = det.get('inputNodes') or []
        types = [inp.get('fieldType') for inp in inputs]
        has_image = any(t == 'IMAGE' for t in types)
        has_text = any(t == 'STRING' for t in types)
        
        info = {
            'id': wid,
            'name': wname,
            'inputs': [{'nodeId': inp.get('nodeId'), 'fieldName': inp.get('fieldName'), 'fieldType': inp.get('fieldType'), 'desc': inp.get('description') or inp.get('descriptionCn'), 'default': inp.get('fieldValue')} for inp in inputs]
        }
        if has_text and not has_image:
            t2i_webapps.append(info)
            print(f"🎨 [纯文生图] {wname:28} (ID: {wid}) -> 节点数: {len(inputs)}", flush=True)
        else:
            i2i_webapps.append(info)
            print(f"🖼️ [图生图/其他] {wname:28} (ID: {wid}) -> 类型: {types}", flush=True)
    except Exception as e:
        print(f"Err {wid}: {e}", flush=True)

print(f"\n找到纯文生图工作流: {len(t2i_webapps)} 个", flush=True)
with open('t2i_webapps.json', 'w', encoding='utf-8') as f:
    json.dump(t2i_webapps, f, ensure_ascii=False, indent=2)

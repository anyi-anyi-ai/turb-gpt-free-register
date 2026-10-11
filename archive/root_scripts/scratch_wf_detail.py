# -*- coding: utf-8 -*-
import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
import requests, json

with open('data/rh_accounts/CatherineAndrews17889LE@outlook.com.json', 'r', encoding='utf-8') as f:
    acc = json.load(f)

token = acc['token']
proxy = acc['proxy']
proxies = {'http': proxy, 'https': proxy} if proxy else None
headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}

wid = '2102677348907102210'
r = requests.post('https://www.runninghub.ai/api/webapp/detail', headers=headers, json={'webappId': wid}, proxies=proxies, timeout=15)
d = r.json().get('data', {})
print('Name:', d.get('name'))
inputs = d.get('inputNodes', [])
print('Input nodes count:', len(inputs))
for inp in inputs:
    nid = inp.get('nodeId')
    nname = inp.get('nodeName')
    fname = inp.get('fieldName')
    ftype = inp.get('fieldType')
    fval = str(inp.get('fieldValue'))[:60]
    fdesc = inp.get('description') or inp.get('descriptionCn') or ''
    print(f"Node {nid} [{nname}]: field={fname}, type={ftype}, desc={fdesc}, default={fval}")

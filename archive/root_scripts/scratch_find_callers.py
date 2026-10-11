# -*- coding: utf-8 -*-
import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
import requests, json, re

with open('data/rh_accounts/CatherineAndrews17889LE@outlook.com.json', 'r', encoding='utf-8') as f:
    acc = json.load(f)

p = acc.get('proxy')
proxies = {'http': p, 'https': p} if p else None

js = requests.get('https://www.runninghub.ai/_nuxt/DSXxOfri.js', proxies=proxies, timeout=20).text
print("JS length:", len(js))

# Let's search for references to createWebAppApi and outputTaskHistoryRes
for name in ['createWebAppApi', 'outputTaskHistoryRes', 'outputHistoryRes']:
    print(f"\n================ Searches for {name} ================")
    matches = [m.start() for m in re.finditer(re.escape(name), js)]
    for idx in matches:
        print(f"\n[Index {idx}]:")
        start = max(0, idx - 150)
        end = min(len(js), idx + 250)
        print(js[start:end].replace('\n', ' '))

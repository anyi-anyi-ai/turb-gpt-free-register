# -*- coding: utf-8 -*-
import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
import requests, re

r = requests.get('https://www.runninghub.ai/ai-detail/2102677348907102210', timeout=20)
links = re.findall(r'(/_nuxt/[a-zA-Z0-9_\-]+\.js)', r.text)
links = list(set(links))
print(f"Total unique nuxt js links: {len(links)}")

for l in links:
    js_url = f"https://www.runninghub.ai{l}"
    try:
        js = requests.get(js_url, timeout=10).text
        for term in ['createWebAppApi', 'outputTaskHistoryRes', 'outputHistoryRes', '/task/webapp/create', '/api/output/']:
            if term in js:
                print(f"Found '{term}' in {l} (len={len(js)})")
    except Exception as e:
        print(f"Error {l}: {e}")

# -*- coding: utf-8 -*-
from curl_cffi import requests
import re

url = "https://www.runninghub.ai/runninghub-api-doc-en/doc-6768554"
r = requests.get(url, impersonate='chrome120', timeout=20)
print("Status:", r.status_code)
html = r.text

clean = re.sub(r'<[^>]+>', ' ', html)
for line in clean.splitlines():
    line = line.strip()
    if len(line) > 10 and any(k in line.lower() for k in ['http', 'api', 'task', 'token', 'curl', 'workflow', 'create', 'query', 'status', 'output']):
        print(line[:120])

# -*- coding: utf-8 -*-
from curl_cffi import requests
import json, re

url = "https://www.runninghub.ai/call-api/api-detail/2133100000000800376"
r = requests.get(url, impersonate='chrome120', timeout=20)
html = r.text

nuxt_match = re.search(r'<script[^>]*>window\.__NUXT__=(.*?);?</script>', html)
if nuxt_match:
    print("Found NUXT data, len:", len(nuxt_match.group(1)))
    # find api docs or curl examples
    text = nuxt_match.group(1)
    for m in re.finditer(r'(http[s]?://[^\s"\',]+|curl[^\"]+)', text):
        val = m.group(1)
        if any(k in val for k in ['task', 'api', 'openapi', 'runninghub']):
            print("Link/Curl:", val[:120])
else:
    print("No NUXT tag, searching raw text:")
    for m in re.finditer(r'curl[^\"]+', html):
        print("Curl:", m.group(0)[:150])

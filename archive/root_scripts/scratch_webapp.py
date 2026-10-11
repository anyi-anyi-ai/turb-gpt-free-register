# -*- coding: utf-8 -*-
from curl_cffi import requests
import re, json

r = requests.get('https://www.runninghub.ai/_nuxt/DSXxOfri.js', impersonate='chrome120', timeout=20)
js = r.text

pattern = r'["\'`](/(?:api|task|uc|webapp)/[^"\'`\s]+)["\'`]'
endpoints = set(re.findall(pattern, js))
for ep in sorted(endpoints):
    if 'webapp' in ep.lower():
        print(ep)

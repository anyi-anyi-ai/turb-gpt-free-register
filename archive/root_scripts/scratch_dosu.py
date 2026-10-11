# -*- coding: utf-8 -*-
from curl_cffi import requests
import re

url = 'https://www.runninghub.ai/_nuxt/DSXxOfri.js'
js = requests.get(url, impersonate='chrome120', timeout=20).text

pattern = r'https?://[a-zA-Z0-9.-]*runninghub\.[a-zA-Z]+[^\s"\'`)]*'
matches = set(re.findall(pattern, js))
for m in sorted(matches):
    print(m)

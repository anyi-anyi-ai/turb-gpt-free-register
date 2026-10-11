# -*- coding: utf-8 -*-
from curl_cffi import requests
r = requests.get('https://www.runninghub.ai/_nuxt/DSXxOfri.js', impersonate='chrome120', timeout=20)
js = r.text
import re
for m in re.finditer(r'outputHistoryRes', js):
    idx = m.start()
    print('Match at', idx)
    print(js[max(0, idx-100):min(len(js), idx+250)].replace('\n', ' '))

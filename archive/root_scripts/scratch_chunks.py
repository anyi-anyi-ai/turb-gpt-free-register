# -*- coding: utf-8 -*-
from curl_cffi import requests
import re

r = requests.get('https://www.runninghub.ai/_nuxt/DSXxOfri.js', impersonate='chrome120', timeout=20)
js = r.text

chunks = re.findall(r'"([A-Za-z0-9_-]+\.js)"', js)
print('Found chunks in bundle:', len(chunks))
unique_chunks = list(dict.fromkeys(chunks))
print('Unique chunks:', len(unique_chunks))

for c in unique_chunks:
    c_url = f'https://www.runninghub.ai/_nuxt/{c}'
    try:
        txt = requests.get(c_url, impersonate='chrome120', timeout=10).text
        if '/task/webapp/create' in txt or 'createWebAppApi' in txt:
            print(f'Hit in {c}!')
            for m in re.finditer(r'(/task/webapp/create|createWebAppApi)', txt):
                idx = m.start()
                print('  ', txt[max(0, idx-80):min(len(txt), idx+200)].replace('\n', ' '))
    except Exception as e:
        pass

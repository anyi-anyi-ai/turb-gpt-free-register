# -*- coding: utf-8 -*-
import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
import requests, json

with open('data/rh_accounts/CatherineAndrews17889LE@outlook.com.json', 'r', encoding='utf-8') as f:
    acc = json.load(f)

token = acc['token']
proxy = acc.get('proxy')
proxies = {'http': proxy, 'https': proxy} if proxy else None
headers = {
    'Authorization': f'Bearer {token}',
    'Accept': 'application/json, text/plain, */*',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Origin': 'https://www.runninghub.ai',
    'Referer': 'https://www.runninghub.ai/ai-detail/2102677348907102210',
    'Content-Type': 'application/json'
}

wid = '2102677348907102210'
r = requests.post('https://www.runninghub.ai/api/webapp/detail', headers=headers, json={'webappId': wid}, proxies=proxies, timeout=15)
data = r.json().get('data', {})
print("Raw inputNodes:")
print(json.dumps(data.get('inputNodes'), ensure_ascii=False, indent=2))

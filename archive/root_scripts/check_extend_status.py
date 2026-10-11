import sqlite3
import json
import urllib.request

conn = sqlite3.connect('turb.sqlite3')
cur = conn.cursor()
cur.execute('SELECT session_jwt, cookies_json FROM suno_accounts LIMIT 1')
jwt, cj = cur.fetchone()
cookies_dict = json.loads(cj)
cookie_header = '; '.join([f'{k}={v}' for k, v in cookies_dict.items() if '__' in k or 'suno' in k or 'session' in k])

url = 'https://studio-api-prod.suno.com/api/feed/v3'
body = json.dumps({
    'cursor': None,
    'limit': 10,
    'filters': {
        'disliked': 'False',
        'trashed': 'False',
        'fromStudioProject': {'presence': 'False'},
        'stem': {'presence': 'False'},
        'stemComplement': 'False',
        'workspace': {'presence': 'True', 'workspaceId': 'default'}
    }
}).encode('utf-8')

opener = urllib.request.build_opener(urllib.request.ProxyHandler({'http': 'http://127.0.0.1:7890', 'https': 'http://127.0.0.1:7890'}))
req = urllib.request.Request(url, data=body, headers={
    'User-Agent': 'Mozilla/5.0',
    'Authorization': f'Bearer {jwt}',
    'Cookie': cookie_header,
    'Content-Type': 'application/json',
    'Accept': 'application/json'
})

res = opener.open(req)
data = json.loads(res.read().decode())
print(f"Total clips: {len(data.get('clips', []))}")
for c in data.get('clips', []):
    meta = c.get('metadata', {})
    print(f"ID: {c.get('id')}")
    print(f"Title: {c.get('title')}")
    print(f"Status: {c.get('status')}")
    print(f"Duration: {meta.get('duration')}")
    print(f"Continue Clip ID: {meta.get('continue_clip_id')}")
    print(f"Continue At: {meta.get('continue_at')}")
    print(f"Image URL: {c.get('image_url')}")
    print("-" * 40)

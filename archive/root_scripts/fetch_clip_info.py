import sqlite3
import json
import urllib.request
import ssl

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

conn = sqlite3.connect('f:/github/turb-gpt-free-register/turb.sqlite3')
cur = conn.cursor()
cur.execute('SELECT session_jwt, cookies_json FROM suno_accounts WHERE email="tanxiulan23426@outlook.com"')
jwt, cookies_json = cur.fetchone()
cookies_dict = json.loads(cookies_json)

cookie_header = '; '.join([f'{k}={v}' for k, v in cookies_dict.items() if '__' in k or 'suno' in k or 'session' in k])

url = 'https://studio-api-prod.suno.com/api/feed/v3'
body = json.dumps({
    "cursor": None,
    "limit": 10,
    "filters": {
        "disliked": "False",
        "trashed": "False",
        "fromStudioProject": {"presence": "False"},
        "stem": {"presence": "False"},
        "stemComplement": "False",
        "workspace": {"presence": "True", "workspaceId": "default"}
    }
}).encode('utf-8')

opener = urllib.request.build_opener(urllib.request.ProxyHandler({'http': 'http://127.0.0.1:7890', 'https': 'http://127.0.0.1:7890'}))
req = urllib.request.Request(url, data=body, headers={
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Authorization': f'Bearer {jwt}',
    'Cookie': cookie_header,
    'Content-Type': 'application/json',
    'Accept': 'application/json'
})

try:
    with opener.open(req, timeout=10) as res:
        data = json.loads(res.read().decode())
        print(f"Total clips in feed: {len(data.get('clips', []))}")
        for c in data.get('clips', []):
            print(f"ID: {c.get('id')}")
            print(f"Title: {c.get('title')}")
            print(f"Status: {c.get('status')}")
            print(f"Duration: {c.get('metadata', {}).get('duration')}")
            print(f"Audio URL: {c.get('audio_url')}")
            print(f"Video URL: {c.get('video_url')}")
            print(f"Image URL: {c.get('image_url')}")
            print("-" * 50)
        with open('feed_result.json', 'w', encoding='utf-8') as out:
            json.dump(data, out, indent=2, ensure_ascii=False)
except Exception as e:
    print('Error:', e)

import sqlite3
import json
import urllib.request

conn = sqlite3.connect('turb.sqlite3')
cur = conn.cursor()
cur.execute('SELECT session_jwt, cookies_json FROM suno_accounts WHERE email="tanxiulan23426@outlook.com"')
jwt, cookies_json = cur.fetchone()
cookies_dict = json.loads(cookies_json)
cookie_header = '; '.join([f'{k}={v}' for k, v in cookies_dict.items() if '__' in k or 'suno' in k or 'session' in k])

for clip_id in ['c861bf72-f4dd-4671-86e3-4a397efb264b', '2630eaad-1654-4064-bca6-b616ca84c286']:
    url = f'https://studio-api-prod.suno.com/api/clips/{clip_id}'
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({'http': 'http://127.0.0.1:7890', 'https': 'http://127.0.0.1:7890'}))
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Authorization': f'Bearer {jwt}',
        'Cookie': cookie_header,
        'Accept': 'application/json'
    })

    try:
        with opener.open(req, timeout=10) as res:
            data = json.loads(res.read().decode())
            print(f"=== CLIP {clip_id} ===")
            print("Title:", data.get('title'))
            print("Status:", data.get('status'))
            print("Duration:", data.get('metadata', {}).get('duration'))
            print("Image URL:", data.get('image_url'))
            print("Web URL:", f"https://suno.com/song/{data.get('id')}")
            print("Is Public:", data.get('is_public'))
    except Exception as e:
        print(f"Error for {clip_id}:", e)

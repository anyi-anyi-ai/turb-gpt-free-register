import sqlite3
import json
import requests
import os

conn = sqlite3.connect('f:/github/turb-gpt-free-register/turb.sqlite3')
cur = conn.cursor()
cur.execute('SELECT email, session_jwt, cookies_json FROM suno_accounts WHERE email="tanxiulan23426@outlook.com"')
row = cur.fetchone()
email, jwt, cookies_json = row
cookies = json.loads(cookies_json)

essential_cookies = {}
for k, v in cookies.items():
    if k.startswith("__client") or k.startswith("__session") or "suno" in k.lower() or "clerk" in k.lower() or "cf_" in k.lower():
        essential_cookies[k] = v

cookie_str = '; '.join([f"{k}={v}" for k, v in essential_cookies.items()])
headers = {
    "Authorization": f"Bearer {jwt}",
    "Cookie": cookie_str,
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
    "Origin": "https://suno.com",
    "Referer": "https://suno.com/"
}

clip_ids = "c861bf72-f4dd-4671-86e3-4a397efb264b,2630eaad-1654-4064-bca6-b616ca84c286"
url = f"https://studio-api-prod.suno.com/api/feed/v2?ids={clip_ids}"

print(f"[*] Querying feed for clips: {clip_ids}...")
r = requests.get(url, headers=headers, proxies={'https': 'http://127.0.0.1:7890'}, timeout=20)
print(f"[*] Feed status: {r.status_code}")
data = r.json()
print(f"[*] Feed data:")
print(json.dumps(data, indent=2, ensure_ascii=False))

os.makedirs("generated_music", exist_ok=True)
clips = data if isinstance(data, list) else data.get("clips", [])
for idx, clip in enumerate(clips):
    audio_url = clip.get("audio_url")
    title = clip.get("title") or f"aurora_clip_{idx+1}"
    cid = clip.get("id")
    status = clip.get("status")
    print(f"\n[CLIP {idx+1}] ID: {cid} | Status: {status} | Title: {title}")
    print(f"Audio URL: {audio_url}")
    print(f"Image URL: {clip.get('image_url')}")
    print(f"Video URL: {clip.get('video_url')}")
    if audio_url:
        local_path = f"generated_music/{idx+1}_{cid[:8]}.mp3"
        print(f"[*] Downloading {audio_url} -> {local_path}...")
        res = requests.get(audio_url, timeout=60)
        if res.status_code == 200:
            with open(local_path, "wb") as f:
                f.write(res.content)
            print(f"[✅] Saved {local_path} ({len(res.content)} bytes)")

import sqlite3
import json
import requests

conn = sqlite3.connect('f:/github/turb-gpt-free-register/turb.sqlite3')
cur = conn.cursor()
cur.execute('SELECT email, clerk_client_id, session_jwt, cookies_json FROM suno_accounts WHERE email="tanxiulan23426@outlook.com"')
row = cur.fetchone()
email, client_id, jwt, cookies_json = row
cookies = json.loads(cookies_json)

# Filter cookies to only suno/clerk relevant ones
essential_cookies = {}
for k, v in cookies.items():
    if k.startswith("__client") or k.startswith("__session") or "suno" in k.lower() or "clerk" in k.lower():
        essential_cookies[k] = v

cookie_str = '; '.join([f"{k}={v}" for k, v in essential_cookies.items()])
headers = {
    "Authorization": f"Bearer {jwt}",
    "Cookie": cookie_str,
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Origin": "https://suno.com",
    "Referer": "https://suno.com/"
}

print(f"[*] Testing billing/info for {email}...")
print(f"[*] Essential cookie count: {len(essential_cookies)}, length: {len(cookie_str)}")
r = requests.get('https://studio-api.prod.suno.com/api/billing/info/', headers=headers, proxies={'https': 'http://127.0.0.1:7890'}, timeout=15)
print("STATUS:", r.status_code)
print("BODY:", r.text)

if r.status_code == 200:
    data = r.json()
    credits = data.get("total_credits_left")
    print(f"[*] SUCCESS! Credits left: {credits}")
    
    # Push to music2api gateway
    from core.suno2api_sync import push_account_to_suno2api
    res = push_account_to_suno2api(
        email=email,
        cookies=essential_cookies,
        session_jwt=jwt,
        clerk_client_id=client_id,
        label=f"{email} (Credits: {credits})"
    )
    print(f"[*] Pushed to music2api: {res}")

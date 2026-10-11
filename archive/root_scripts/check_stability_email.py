import time
import json
import sqlite3
from core.outlook_client import OutlookAccount, _CONTEXT_CACHE, _cache_key, _fetch_via, _http_session, _parse_email_ts

email = "VeronicaClark27425II@outlook.com"
conn = sqlite3.connect('turb.sqlite3')
cur = conn.cursor()
cur.execute('SELECT payload FROM email_pool WHERE email = ?', (email,))
payload_str = cur.fetchone()[0]
payload = json.loads(payload_str)

account = OutlookAccount(
    email=email,
    password=payload.get("password", ""),
    client_id=payload.get("client_id"),
    refresh_token=payload.get("refresh_token")
)
_CONTEXT_CACHE[_cache_key(email)] = account

session = _http_session()
print(f"[*] Checking emails for {email}...")
messages = _fetch_via(session, "graph", account)
print(f"[*] Total emails in inbox: {len(messages)}")
for m in messages[:5]:
    print("Subject:", m.get("subject"))
    print("From:", m.get("from") or m.get("sendEmail"))
    print("Date:", m.get("receivedDateTime") or m.get("date"))
    print("-" * 40)

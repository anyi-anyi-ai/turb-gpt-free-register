# -*- coding: utf-8 -*-
import json
import sqlite3
from pathlib import Path

p = Path(r"f:\github\gptGrok2api\data\accounts.json")
if not p.exists():
    print("accounts.json not found")
    exit(1)

data = json.loads(p.read_text(encoding="utf-8"))

conn = sqlite3.connect(r"f:\github\turb-gpt-free-register\turb.sqlite3")
conn.row_factory = sqlite3.Row
rows = conn.execute("SELECT * FROM grok_accounts WHERE status='healthy'").fetchall()
grok_db = {r["email"].lower(): dict(r) for r in rows}

print(f"Found {len(grok_db)} healthy Grok accounts in DB:")
for em in grok_db:
    print(f"  - {em}")

count = 0
for x in data:
    em = str(x.get("email") or "").lower().strip()
    if em in grok_db:
        g = grok_db[em]
        x["source_type"] = "grok"
        x["access_token"] = g["sso_token"]
        x["login_password"] = g["password"]
        x["status"] = "正常"
        x["enabled"] = True
        x["disabled"] = False
        x["fail"] = 0
        x["failure_count"] = 0
        x["chat_failure_count"] = 0
        x["last_error_kind"] = None
        x["last_error_status"] = None
        x["last_error_message"] = None
        x["survival_status"] = "ok"
        x["survival_last_probe_status"] = "ok"
        x["survival_check_error"] = None
        count += 1

p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"Successfully refreshed and reset {count} Grok accounts in gptGrok2api data/accounts.json")

# 同步写入 grok_accounts.json (用于 GPTGrok2API 控制台的「Grok」专用账号管理 Tab)
grok_p = Path(r"f:\github\gptGrok2api\data\grok_accounts.json")
grok_items = []
for em, g in grok_db.items():
    oauth_state = None
    if g.get("access_token"):
        oauth_state = {
            "status": "authorized",
            "attempted_at": g.get("updated_at") or "",
            "error": "",
            "attempts": 1
        }
    item = {
        "id": f"grok-{g['id']}",
        "email": g["email"],
        "password": g.get("password") or "",
        "sso": g.get("sso_token") or g.get("sso_rw") or "",
        "access_token": g.get("access_token") or "",
        "refresh_token": g.get("refresh_token") or "",
        "status": "active",
        "enabled": True,
        "source_type": "protocol",
        "created_at": g.get("created_at") or "",
        "updated_at": g.get("updated_at") or "",
    }
    if oauth_state:
        item["oauth_authorization"] = oauth_state
    if g.get("probe_tps"):
        item["runtime"] = {
            "probe_tps": g["probe_tps"],
            "probe_reasoning_tokens": g.get("probe_reasoning_tokens", 0)
        }
    grok_items.append(item)

grok_p.write_text(json.dumps(grok_items, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"Successfully refreshed and synced {len(grok_items)} Grok accounts to gptGrok2api data/grok_accounts.json")

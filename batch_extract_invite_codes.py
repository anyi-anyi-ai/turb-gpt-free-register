# -*- coding: utf-8 -*-
import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
import requests
import glob, json, base64, time
import sqlite3

files = glob.glob('data/rh_accounts/*.json')
print(f"Found {len(files)} account files to extract invite codes.\n")

results = []

for f in files:
    with open(f, 'r', encoding='utf-8') as fp:
        acc = json.load(fp)

    email = acc.get('email')
    token = acc.get('token')
    if not token:
        continue
    proxy = acc.get('proxy')

    try:
        payload = json.loads(base64.b64decode(token.split('.')[1] + '==').decode('utf-8'))
        sub = payload.get('sub')
    except Exception:
        continue

    headers = {
        'Authorization': f'Bearer {token}',
        'Accept': 'application/json, text/plain, */*',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Origin': 'https://www.runninghub.ai',
        'Referer': 'https://www.runninghub.ai/',
        'Content-Type': 'application/json'
    }

    proxies = {"http": proxy, "https": proxy} if proxy else None
    try:
        r = requests.post("https://www.runninghub.ai/uc/getUserInfo", headers=headers, json={'userId': sub}, proxies=proxies, timeout=15)
        d = r.json().get('data') or {}
        code = d.get('inviteCode')
        total_coin = d.get('totalCoin', '0')
        nick = d.get('nickName', '')

        # Update JSON file
        acc['invite_code'] = code
        acc['user_id'] = sub
        acc['total_coin'] = total_coin
        with open(f, 'w', encoding='utf-8') as fp:
            json.dump(acc, fp, ensure_ascii=False, indent=2)

        results.append({
            'email': email,
            'invite_code': code,
            'total_coin': total_coin,
            'sub': sub,
            'nick': nick
        })
        print(f"✅ {email:36} -> 邀请码: {code} | 算力: {total_coin} | ID: {sub}")
    except Exception as e:
        print(f"❌ {email} 提取失败: {e}")

    time.sleep(0.5)

# Also update gateway db if exists
try:
    conn = sqlite3.connect('f:/github/runninghub2api/data/rh_gateway.db')
    cur = conn.cursor()
    for r in results:
        cur.execute("UPDATE accounts SET remark = ? WHERE email = ?", (f"邀请码: {r['invite_code']}", r['email']))
    conn.commit()
    conn.close()
    print("\n✅ 网关数据库已同步更新各账号备注与邀请码！")
except Exception as e:
    print("网关更新异常:", e)

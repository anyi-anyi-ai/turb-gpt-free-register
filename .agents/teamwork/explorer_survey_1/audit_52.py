import sys, sqlite3, json, html, re
from pathlib import Path
_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from core.outlook_client import get_account_context, _ms_access_token, _fetch_outlook_rest_messages, _ms_http

conn = sqlite3.connect("turb.sqlite3")
cur = conn.cursor()
cur.execute("SELECT id, uned_identifier, email, payload_json FROM uned_accounts WHERE forwarding_enabled=0 ORDER BY id ASC")
rows = cur.fetchall()
conn.close()

client = _ms_http()
has_access_mail = []
has_token_url = []
no_token_url = []
error_accounts = []

print(f"Auditing {len(rows)} accounts for Outlook messages...")

for idx, (aid, uid, email, p_str) in enumerate(rows, 1):
    ctx = get_account_context(email)
    if not ctx:
        error_accounts.append((aid, uid, email, "no_context"))
        continue
    try:
        tok, kind = _ms_access_token(ctx, http=client)
        msgs = _fetch_outlook_rest_messages(client, tok)
        subjs = [str(m.get("subject", "")).lower() for m in msgs]
        has_acc = any("datos de acceso" in s or "access data" in s for s in subjs)
        
        act_url = None
        for m in msgs:
            content = str(m.get("body", "")) or str(m.get("bodyPreview", ""))
            if "password.aspx" in content:
                unescaped = html.unescape(content)
                m_url = re.search(r'https://sso\.uned\.es/sso/password\.aspx\?[^\s"\'<>]+', unescaped)
                if m_url:
                    act_url = m_url.group(0)
                    break
        if has_acc:
            has_access_mail.append((aid, uid, email))
        elif act_url:
            has_token_url.append((aid, uid, email, act_url))
        else:
            no_token_url.append((aid, uid, email, [m.get("subject") for m in msgs[:3]]))
    except Exception as e:
        error_accounts.append((aid, uid, email, str(e)))

print("="*60)
print(f"AUDIT RESULTS (Total: {len(rows)}):")
print(f"  1. Has 'Datos de Acceso' confirmation: {len(has_access_mail)}")
print(f"  2. Has initial password.aspx link (no access mail): {len(has_token_url)}")
print(f"  3. No activation or access email: {len(no_token_url)}")
print(f"  4. Outlook error: {len(error_accounts)}")
print("="*60)
if has_access_mail:
    print("Sample with access mail (First 5):", has_access_mail[:5])
if has_token_url:
    print("Sample with token URL only (First 5):", [(x[0], x[1], x[2]) for x in has_token_url[:5]])
if no_token_url:
    print("Sample with no token URL:", no_token_url[:5])
if error_accounts:
    print("Sample errors:", error_accounts[:5])

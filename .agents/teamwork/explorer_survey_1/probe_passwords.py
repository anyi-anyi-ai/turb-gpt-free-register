import json, sqlite3, sys, time
sys.stdout.reconfigure(encoding='utf-8')
from playwright.sync_api import sync_playwright

proxy = {'server': 'http://127.0.0.1:2260', 'username': 'NL', 'password': 'resin_proxy_c3d7b8e1f0a2496582d9183b'}

def add_asset_blocking(page):
    page.route("**/*.{woff,woff2,ttf,eot,png,jpg,jpeg,gif,svg,ico}", lambda r: r.abort())
    page.route("**/gtm.js**", lambda r: r.abort())
    page.route("**/google-analytics.com/**", lambda r: r.abort())
    page.route("**/googletagmanager.com/**", lambda r: r.abort())

conn = sqlite3.connect("turb.sqlite3")
cur = conn.cursor()
cur.execute("SELECT id, uned_identifier, email, payload_json FROM uned_accounts WHERE forwarding_enabled=0 ORDER BY id ASC LIMIT 5")
rows = cur.fetchall()
conn.close()

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, proxy=proxy, args=["--disable-blink-features=AutomationControlled"])
    for idx, (acc_id, uid, email, p_str) in enumerate(rows, 1):
        p_obj = json.loads(p_str)
        pwd = p_obj.get("extra", {}).get("password", "")
        doc_num = p_obj.get("doc_number", "")
        
        ctx = browser.new_context(locale='es-ES', viewport={'width': 1280, 'height': 800})
        page = ctx.new_page()
        add_asset_blocking(page)
        
        login_url = "https://sso.uned.es/sso/index.aspx?URL=https%3a%2f%2flogin.uned.es%2fssouned%2flogin.jsp"
        try:
            page.goto(login_url, wait_until='commit', timeout=20000)
            page.wait_for_selector('#ContentPlaceHolder1_ssousername', timeout=12000)
            page.fill('#ContentPlaceHolder1_ssousername', uid)
            page.fill('#ContentPlaceHolder1_password', pwd)
            page.click('#ContentPlaceHolder1_Button1')
            page.wait_for_timeout(4000)
            
            body = page.locator("body").inner_text().lower()
            url = page.url
            if "login.jsp" in url or "salida.aspx" in url or "serviwebng" in url or "sso.uned.es" not in url:
                status = "LOGIN_OK"
            elif "bloqueada" in body:
                status = "LOCKED"
            elif "incorrecto" in body:
                status = "BAD_PASSWORD"
            else:
                status = f"UNKNOWN: {url}"
            print(f"[{idx}/5] ID={acc_id} UID={uid} PWD={pwd[:6]}... -> {status}")
        except Exception as e:
            print(f"[{idx}/5] ID={acc_id} UID={uid} -> EXCEPTION: {e}")
        finally:
            ctx.close()
    browser.close()

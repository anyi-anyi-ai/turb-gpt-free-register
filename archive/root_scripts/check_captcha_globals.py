import sqlite3
import json
import time
from core.cloakbrowser_driver import build_cloak_driver

conn = sqlite3.connect('f:/github/turb-gpt-free-register/turb.sqlite3')
cur = conn.cursor()
cur.execute('SELECT email, session_jwt, cookies_json FROM suno_accounts WHERE email="tanxiulan23426@outlook.com"')
row = cur.fetchone()
email, jwt, cookies_json = row
cookies_dict = json.loads(cookies_json)

driver, _ = build_cloak_driver(proxy="http://127.0.0.1:7890", headless=True)

try:
    driver.get("https://suno.com/")
    time.sleep(2)
    driver.page.context.add_cookies([{'name': k, 'value': v, 'url': 'https://suno.com'} for k, v in cookies_dict.items()])
    driver.get("https://suno.com/create")
    time.sleep(6)
    
    res = driver.page.evaluate('''() => ({
        hasTurnstile: typeof window.turnstile !== 'undefined',
        hasHcaptcha: typeof window.hcaptcha !== 'undefined',
        hasGrecaptcha: typeof window.grecaptcha !== 'undefined',
        scripts: Array.from(document.querySelectorAll('script[src]')).map(s => s.src).filter(s => s.includes('turnstile') || s.includes('captcha') || s.includes('challenge') || s.includes('datadome') || s.includes('arkose')),
        clerkObj: typeof window.Clerk !== 'undefined'
    })''')
    print("Captcha Globals:", json.dumps(res, indent=2))
finally:
    driver.quit()

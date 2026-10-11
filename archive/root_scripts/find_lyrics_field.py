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
    
    info = driver.page.evaluate('''() => {
        const el = Array.from(document.querySelectorAll('*')).find(e => e.innerText && e.innerText.includes('Start writing lyrics'));
        const editables = Array.from(document.querySelectorAll('[contenteditable="true"]')).map(e => ({
            tag: e.tagName,
            className: e.className,
            role: e.getAttribute('role'),
            text: e.innerText
        }));
        return {
            lyricsPlaceholderEl: el ? { tag: el.tagName, className: el.className, html: el.outerHTML.substring(0, 300) } : null,
            editables: editables
        };
    }''')
    print("Found:", json.dumps(info, indent=2, ensure_ascii=False))
finally:
    driver.quit()

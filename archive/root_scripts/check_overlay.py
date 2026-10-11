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
    
    # Check overlays, modals, or dialogs
    overlay_info = driver.page.evaluate('''() => {
        const dialogs = Array.from(document.querySelectorAll('[role="dialog"], [role="alertdialog"], .modal, [class*="overlay"], [class*="backdrop"]')).map(d => ({
            tag: d.tagName,
            role: d.getAttribute('role'),
            className: d.className,
            text: d.innerText
        }));
        
        // Element at center of screen (width/2, height/2)
        const centerEl = document.elementFromPoint(window.innerWidth / 2, window.innerHeight / 2);
        
        return {
            dialogs,
            centerElement: centerEl ? {
                tag: centerEl.tagName,
                className: centerEl.className,
                text: centerEl.innerText ? centerEl.innerText.substring(0, 100) : null
            } : null
        };
    }''')
    print("Overlay info:", json.dumps(overlay_info, indent=2, ensure_ascii=False))
    driver.page.screenshot(path="overlay_check.png")
finally:
    driver.quit()

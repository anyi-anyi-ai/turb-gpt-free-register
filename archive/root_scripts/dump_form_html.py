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
    
    html = driver.page.evaluate('''() => {
        // Find the panel that contains "Create" button
        const createBtn = Array.from(document.querySelectorAll('button')).find(b => b.textContent.trim() === 'Create');
        if (!createBtn) return "Create button not found";
        
        let p = createBtn.parentElement;
        for (let i = 0; i < 5; i++) {
            if (p.parentElement && p.parentElement.tagName !== 'BODY') {
                p = p.parentElement;
            }
        }
        return p.innerHTML.substring(0, 4000);
    }''')
    print("Form HTML:\n", html[:3000])
finally:
    driver.quit()

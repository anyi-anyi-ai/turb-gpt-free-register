import sqlite3
import json
import time
from core.cloakbrowser_driver import build_cloak_driver

conn = sqlite3.connect('turb.sqlite3')
cur = conn.cursor()
cur.execute('SELECT email, session_jwt, cookies_json FROM suno_accounts WHERE email="tanxiulan23426@outlook.com"')
email, jwt, cookies_json = cur.fetchone()
cookies_dict = json.loads(cookies_json)

driver, _ = build_cloak_driver(proxy='http://127.0.0.1:7890', headless=True)
try:
    driver.get('https://suno.com/')
    time.sleep(2)
    driver.page.context.add_cookies([{'name': k, 'value': v, 'url': 'https://suno.com'} for k, v in cookies_dict.items()])
    driver.get('https://suno.com/create')
    time.sleep(6)
    
    # Click More options
    driver.page.evaluate('''() => {
        const btns = Array.from(document.querySelectorAll('button[aria-label="More options"]'));
        if (btns.length > 0) btns[0].click();
    }''')
    time.sleep(1.5)
    
    # Hover / click Edit menu item
    print("[*] Interacting with Edit submenu trigger...")
    driver.page.evaluate('''() => {
        const editItem = Array.from(document.querySelectorAll('[role="menuitem"]')).find(el => (el.innerText || '').trim() === 'Edit');
        if (editItem) {
            editItem.dispatchEvent(new PointerEvent('pointerenter', { bubbles: true }));
            editItem.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));
            editItem.dispatchEvent(new MouseEvent('mouseenter', { bubbles: true }));
            editItem.click();
        }
    }''')
    time.sleep(2)
    driver.page.screenshot(path="after_edit_hover.png")
    
    # Check what items are now visible
    items = driver.page.evaluate('''() => {
        return Array.from(document.querySelectorAll('[role="menuitem"], [role="option"], button')).map(el => ({
            text: (el.innerText || '').trim(),
            role: el.getAttribute('role'),
            className: el.className
        }));
    }''')
    print("[*] All menu items visible:", json.dumps(items, indent=2, ensure_ascii=False))

finally:
    driver.quit()

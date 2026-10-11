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
    
    # 1. Click More options on the song
    driver.page.evaluate('''() => {
        const btns = Array.from(document.querySelectorAll('button[aria-label="More options"]'));
        if (btns.length > 0) btns[0].click();
    }''')
    time.sleep(1.5)
    
    # 2. Hover / click Remix menu item
    print("[*] Interacting with Remix submenu trigger...")
    driver.page.evaluate('''() => {
        const remixItem = Array.from(document.querySelectorAll('[role="menuitem"]')).find(el => (el.innerText || '').trim() === 'Remix');
        if (remixItem) {
            // Dispatch pointerenter, mouseover, pointermove, click
            remixItem.dispatchEvent(new PointerEvent('pointerenter', { bubbles: true }));
            remixItem.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));
            remixItem.dispatchEvent(new MouseEvent('mouseenter', { bubbles: true }));
            remixItem.click();
        }
    }''')
    time.sleep(2)
    driver.page.screenshot(path="after_remix_hover.png")
    
    # 3. Check what items are now visible
    items = driver.page.evaluate('''() => {
        return Array.from(document.querySelectorAll('[role="menuitem"], [role="option"], button')).map(el => ({
            text: (el.innerText || '').trim(),
            role: el.getAttribute('role'),
            className: el.className
        })).filter(i => ['Extend', 'Cover', 'Reuse Prompt', 'Reuse Style', 'Infill', 'Crop'].includes(i.text));
    }''')
    print("[*] Submenu items visible:", json.dumps(items, indent=2, ensure_ascii=False))
    
    # 4. If Extend found, click it!
    extend_clicked = driver.page.evaluate('''() => {
        const ext = Array.from(document.querySelectorAll('[role="menuitem"], [role="option"], button')).find(el => (el.innerText || '').trim() === 'Extend');
        if (ext) {
            ext.click();
            return "Clicked Extend!";
        }
        return "Extend item not found";
    }''')
    print("[*] Extend click:", extend_clicked)
    time.sleep(3)
    driver.page.screenshot(path="after_extend_clicked_final.png")
    
    # 5. Check what Create form shows
    ui_status = driver.page.evaluate('''() => {
        return {
            text: document.querySelector('[class*="create"], form, main')?.innerText?.slice(0, 500)
        };
    }''')
    print("[*] UI text:", ui_status.get("text"))

finally:
    driver.quit()

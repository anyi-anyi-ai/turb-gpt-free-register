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
    
    # Track any requests
    driver.page.on("request", lambda r: print(f"[REQ] {r.method} {r.url}"))
    
    # Click "More options"
    res = driver.page.evaluate('''() => {
        const btns = Array.from(document.querySelectorAll('button[aria-label="More options"]'));
        if (btns.length > 0) {
            btns[0].click();
            return { clicked: true, count: btns.length };
        }
        return { clicked: false, count: 0 };
    }''')
    print("[*] Click result:", res)
    time.sleep(2)
    driver.page.screenshot(path="after_click_more_btn.png")
    
    # Dump popup menu items
    menu_items = driver.page.evaluate('''() => {
        const items = Array.from(document.querySelectorAll('[role="menuitem"], [role="option"], [data-radix-collection-item], button')).filter(el => {
            const txt = (el.innerText || '').trim();
            return txt.length > 0 && txt.length < 50 && (el.closest('[role="menu"]') || el.closest('[data-radix-popper-content-wrapper]'));
        }).map(el => ({
            text: el.innerText.trim(),
            role: el.getAttribute('role'),
            className: el.className
        }));
        return items;
    }''')
    print("[*] Menu items:", json.dumps(menu_items, indent=2, ensure_ascii=False))
    
    # If Download is in menu, let's see what happens if we click it
    click_dl = driver.page.evaluate('''() => {
        const dl = Array.from(document.querySelectorAll('*')).find(el => el.innerText && el.innerText.trim() === 'Download');
        if (dl) {
            dl.click();
            return "Clicked Download";
        }
        return "Download not found";
    }''')
    print("[*] Download click attempt:", click_dl)
    time.sleep(2)
    driver.page.screenshot(path="after_download_clicked.png")
    
    # Check sub-menu if any (e.g. Audio / Video)
    sub_items = driver.page.evaluate('''() => {
        return Array.from(document.querySelectorAll('[role="menuitem"], button')).filter(el => {
            const txt = (el.innerText || '').trim();
            return txt.includes('Audio') || txt.includes('Video') || txt.includes('MP3') || txt.includes('WAV');
        }).map(el => el.innerText.trim());
    }''')
    print("[*] Sub items:", sub_items)
    
finally:
    driver.quit()

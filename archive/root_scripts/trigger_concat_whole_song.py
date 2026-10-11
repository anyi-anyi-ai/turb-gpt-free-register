import sqlite3
import json
import time
from core.cloakbrowser_driver import build_cloak_driver

conn = sqlite3.connect('turb.sqlite3')
cur = conn.cursor()
cur.execute('SELECT session_jwt, cookies_json FROM suno_accounts LIMIT 1')
jwt, cj = cur.fetchone()
cookies_dict = json.loads(cj)

driver, _ = build_cloak_driver(proxy='http://127.0.0.1:7890', headless=True)
try:
    print("[*] Navigating to suno.com/create...")
    driver.get('https://suno.com/')
    time.sleep(2)
    driver.page.context.add_cookies([{'name': k, 'value': v, 'url': 'https://suno.com'} for k, v in cookies_dict.items()])
    driver.get('https://suno.com/create')
    time.sleep(6)
    
    # Intercept requests
    driver.page.on("request", lambda r: print(f"[REQ] {r.method} {r.url}") if 'concat' in r.url or 'generate' in r.url or 'whole' in r.url else None)
    
    # Click More Options on the first song (Extend 1 clip: c636e20f)
    print("[*] Clicking More options on Extend 1 clip...")
    driver.page.evaluate('''() => {
        const btns = Array.from(document.querySelectorAll('button[aria-label="More options"]'));
        if (btns.length > 0) btns[0].click();
    }''')
    time.sleep(1.5)
    
    # Inspect menu items
    menu_items = driver.page.evaluate('''() => {
        const items = Array.from(document.querySelectorAll('[role="menuitem"], [role="option"], button, div')).filter(el => {
            const txt = (el.innerText || '').trim();
            return txt.length > 0 && txt.length < 50 && (el.closest('[role="menu"]') || el.closest('[data-radix-popper-content-wrapper]'));
        }).map(el => el.innerText.trim());
        return Array.from(new Set(items));
    }''')
    print("[*] First level menu items on Extend clip:", menu_items)
    
    # Check Edit submenu
    print("[*] Checking Edit submenu...")
    driver.page.evaluate('''() => {
        const editItem = Array.from(document.querySelectorAll('[role="menuitem"]')).find(el => (el.innerText || '').trim() === 'Edit');
        if (editItem) {
            editItem.dispatchEvent(new PointerEvent('pointerenter', { bubbles: true }));
            editItem.click();
        }
    }''')
    time.sleep(1.5)
    
    edit_sub = driver.page.evaluate('''() => {
        const items = Array.from(document.querySelectorAll('[role="menuitem"], button')).map(el => (el.innerText || '').trim()).filter(Boolean);
        return Array.from(new Set(items));
    }''')
    print("[*] Submenu items on Extend clip:", edit_sub)
    
    # Click "Get Whole Song"
    print("[*] Looking for 'Get Whole Song'...")
    click_concat = driver.page.evaluate('''() => {
        const concatBtn = Array.from(document.querySelectorAll('[role="menuitem"], button, div')).find(el => {
            const txt = (el.innerText || '').trim().toLowerCase();
            return txt.includes('get whole song') || txt.includes('whole song');
        });
        if (concatBtn) {
            concatBtn.click();
            return "Clicked Get Whole Song!";
        }
        return "Get Whole Song not found";
    }''')
    print("[*] Result:", click_concat)
    time.sleep(3)
    driver.page.screenshot(path="after_click_get_whole_song.png")
    
    # Monitor for 60s
    for i in range(6):
        time.sleep(10)
        driver.page.screenshot(path=f"concat_progress_{i}.png")
        print(f"[{i*10}s] Monitoring whole song concat...")

finally:
    driver.quit()

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
    
    # Intercept all requests
    driver.page.on("request", lambda r: print(f"[REQ] {r.method} {r.url}"))
    
    # Click More options on the Extend clip (first song card)
    print("[*] Clicking More options...")
    driver.page.evaluate('''() => {
        const btns = Array.from(document.querySelectorAll('button[aria-label="More options"]'));
        if (btns.length > 0) btns[0].click();
    }''')
    time.sleep(1.5)
    
    # Click "Get Full Song"
    print("[*] Clicking 'Get Full Song'...")
    click_res = driver.page.evaluate('''() => {
        const btn = Array.from(document.querySelectorAll('[role="menuitem"], button, div, span')).find(el => {
            const txt = (el.innerText || '').trim();
            return txt === 'Get Full Song' || txt.includes('Full Song');
        });
        if (btn) {
            btn.click();
            return { clicked: true, text: btn.innerText };
        }
        return { clicked: false };
    }''')
    print("[*] Click result:", click_res)
    time.sleep(3)
    driver.page.screenshot(path="after_click_get_full_song.png")
    
    # Poll for full song generation
    print("[*] Waiting for full song merge to trigger...")
    for i in range(12):
        time.sleep(10)
        driver.page.screenshot(path=f"full_song_progress_{i}.png")
        print(f"[{i*10}s] Monitoring full song merge...")
        
    driver.page.screenshot(path="final_full_song_workspace.png")

finally:
    driver.quit()

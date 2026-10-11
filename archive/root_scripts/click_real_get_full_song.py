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
    concat_reqs = []
    def on_req(r):
        if any(x in r.url for x in ['concat', 'generate', 'clips', 'song']):
            print(f"[CONCAT REQ] {r.method} {r.url}")
            concat_reqs.append({"url": r.url, "method": r.method, "post_data": r.post_data})
    driver.page.on("request", on_req)
    
    # Open more menu on first song (c636e20f, 1:36)
    print("[*] Opening more options...")
    driver.page.evaluate('''() => {
        const btns = Array.from(document.querySelectorAll('button[aria-label="More options"]'));
        if (btns.length > 0) btns[0].click();
    }''')
    time.sleep(1.5)
    
    # Click 'Get Full Song' button precisely
    print("[*] Clicking 'Get Full Song' button...")
    res = driver.page.evaluate('''() => {
        const btns = Array.from(document.querySelectorAll('button')).filter(b => (b.innerText || '').trim() === 'Get Full Song');
        if (btns.length > 0) {
            btns[0].click();
            return { clicked: true, count: btns.length, html: btns[0].outerHTML };
        }
        return { clicked: false, count: 0 };
    }''')
    print("[*] Click result:", json.dumps(res, indent=2))
    time.sleep(3)
    driver.page.screenshot(path="after_click_real_get_full_song.png")
    
    # Poll for 60 seconds
    print("[*] Polling for full song clip to appear...")
    for i in range(12):
        time.sleep(5)
        count = driver.page.evaluate('''() => {
            return Array.from(document.querySelectorAll('div')).filter(d => d.innerText && d.innerText.includes('频率共振') && d.innerText.includes('V6-MINI')).length;
        }''')
        print(f"[{i*5}s] Workspace tracks count indicator:", count)

finally:
    driver.quit()

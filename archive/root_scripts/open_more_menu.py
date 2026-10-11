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
    print("[*] Navigating to suno.com/create...")
    driver.get("https://suno.com/")
    time.sleep(2)
    driver.page.context.add_cookies([{'name': k, 'value': v, 'url': 'https://suno.com'} for k, v in cookies_dict.items()])
    driver.get("https://suno.com/create")
    time.sleep(6)
    
    # Intercept any download network requests
    dl_requests = []
    def on_req(r):
        if any(x in r.url for x in ["download", "export", "audio", "mp3", "wav"]):
            print(f"[REQ] {r.method} {r.url}")
            dl_requests.append({"url": r.url, "method": r.method})
    driver.page.on("request", on_req)

    # Hover over song card and click three dots
    print("[*] Hovering over song card...")
    driver.page.evaluate("""() => {
        // Find all three-dots buttons in the workspace list
        const buttons = Array.from(document.querySelectorAll('button')).filter(b => {
            const svg = b.querySelector('svg');
            const txt = b.innerText || b.textContent || '';
            const aria = b.getAttribute('aria-label') || '';
            return aria.toLowerCase().includes('more') || txt.includes('···') || (svg && b.className.includes('hxc-btn'));
        });
        console.log("Found buttons:", buttons.length);
        // Find the one corresponding to the first song row
        const row = document.querySelector('[class*="song-row"], [class*="track"], tr') || document.body;
        const rowBtn = Array.from(document.querySelectorAll('button')).find(b => b.getAttribute('aria-haspopup') === 'menu' || b.getAttribute('aria-expanded') !== null);
        if (rowBtn) {
            rowBtn.click();
        } else {
            // Click last button on the first card
            const cards = Array.from(document.querySelectorAll('div')).filter(d => d.innerText && d.innerText.includes('频率共振') && d.innerText.includes('1:28'));
            if (cards.length > 0) {
                const btn = cards[0].querySelectorAll('button');
                if (btn.length > 0) btn[btn.length - 1].click();
            }
        }
    }""")
    time.sleep(2)
    driver.page.screenshot(path="after_click_more_menu.png")
    
    # Check menu items
    menu_items = driver.page.evaluate("""() => {
        const items = Array.from(document.querySelectorAll('[role="menuitem"], [role="option"], [class*="menu-item"]')).map(i => ({
            text: i.innerText,
            role: i.getAttribute('role'),
            className: i.className
        }));
        return items;
    }""")
    print("[*] Menu items found:", json.dumps(menu_items, indent=2, ensure_ascii=False))

finally:
    driver.quit()

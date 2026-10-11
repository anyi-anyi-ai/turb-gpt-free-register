import sqlite3
import json
import time
import os
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
    
    # Intercept downloads
    download_urls = []
    def on_req(r):
        if "download" in r.url or ".mp3" in r.url or ".m4a" in r.url or ".wav" in r.url:
            print(f"[DOWNLOAD REQUEST] {r.method} {r.url}")
            download_urls.append(r.url)
    driver.page.on("request", on_req)

    # Click the first song card's more button (three dots)
    print("[*] Finding more buttons...")
    more_btns = driver.page.locator("button:has-text('...'), button[aria-label*='More' i], div[class*='relative'] button:has-text('···')").all()
    if not more_btns:
        # Locate the three dots button by SVG or class
        more_btns = driver.page.locator("button.hxc-btn-base.hxc-btn-variant-tertiary-legacy").all()
        
    print(f"[*] Found {len(more_btns)} potential buttons")
    
    # Let's inspect the options menu by clicking the first one
    if more_btns:
        more_btns[0].click()
        time.sleep(1)
        driver.page.screenshot(path="more_menu_open.png")
        menu_items = driver.page.locator("[role='menuitem'], button").all_text_contents()
        print("[*] Menu items:", [m for m in menu_items if m.strip()])
        
        # Click Download Audio if available
        dl_btn = driver.page.locator("text='Download Audio', text='Audio', text='Download'").first
        if dl_btn.count() > 0:
            print("[*] Clicking Download Audio...")
            with driver.page.expect_download(timeout=15000) as download_info:
                dl_btn.click()
            download = download_info.value
            os.makedirs("generated_music", exist_ok=True)
            save_path = "generated_music/频率共振_极光变奏_Clip1.mp3"
            download.save_as(save_path)
            print(f"[✅] Successfully downloaded to {save_path} ({os.path.getsize(save_path)} bytes)!")

finally:
    driver.quit()

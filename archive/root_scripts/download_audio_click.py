import sqlite3
import json
import time
import os
from core.cloakbrowser_driver import build_cloak_driver

conn = sqlite3.connect('turb.sqlite3')
cur = conn.cursor()
cur.execute('SELECT email, session_jwt, cookies_json FROM suno_accounts WHERE email="tanxiulan23426@outlook.com"')
email, jwt, cookies_json = cur.fetchone()
cookies_dict = json.loads(cookies_json)

os.makedirs('f:/github/turb-gpt-free-register/generated_music', exist_ok=True)

driver, _ = build_cloak_driver(proxy='http://127.0.0.1:7890', headless=True)
try:
    driver.get('https://suno.com/')
    time.sleep(2)
    driver.page.context.add_cookies([{'name': k, 'value': v, 'url': 'https://suno.com'} for k, v in cookies_dict.items()])
    driver.get('https://suno.com/create')
    time.sleep(6)
    
    download_info = []
    def on_dl(download):
        print(f"[DOWNLOAD EVENT] Suggested filename: {download.suggested_filename}, url: {download.url}")
        dest_path = os.path.join('f:/github/turb-gpt-free-register/generated_music', download.suggested_filename)
        download.save_as(dest_path)
        print(f"[DOWNLOAD SAVED] Saved to: {dest_path}, size: {os.path.getsize(dest_path)}")
        download_info.append(dest_path)
        
    driver.page.on("download", on_dl)
    
    # Intercept requests
    driver.page.on("request", lambda r: print(f"[REQ] {r.method} {r.url}") if any(k in r.url for k in ['audio', 'mp3', 'wav', 'cdn', 'download', 'clip']) else None)
    
    # Click More Options
    driver.page.evaluate('''() => {
        const btns = Array.from(document.querySelectorAll('button[aria-label="More options"]'));
        if (btns.length > 0) btns[0].click();
    }''')
    time.sleep(1.5)
    
    # Click Download
    driver.page.evaluate('''() => {
        const dl = Array.from(document.querySelectorAll('*')).find(el => el.innerText && el.innerText.trim() === 'Download');
        if (dl) dl.click();
    }''')
    time.sleep(1.5)
    
    # Click Audio
    print("[*] Clicking 'Audio' sub-menu item...")
    driver.page.evaluate('''() => {
        const audioItem = Array.from(document.querySelectorAll('[role="menuitem"], button, div')).find(el => {
            return (el.innerText || '').trim() === 'Audio';
        });
        if (audioItem) {
            audioItem.click();
            console.log("Audio clicked!");
        }
    }''')
    
    # Wait for download event
    print("[*] Waiting for download to complete...")
    for _ in range(15):
        time.sleep(1)
        if download_info:
            print("[*] Download captured successfully!")
            break
            
    driver.page.screenshot(path="after_audio_download_clicked.png")

finally:
    driver.quit()

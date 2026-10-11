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
    
    # Intercept all requests
    all_reqs = []
    def on_req(r):
        all_reqs.append({"url": r.url, "method": r.method, "resource_type": r.resource_type})
        if r.resource_type in ("media", "fetch", "xhr") and any(x in r.url for x in ["clip", "audio", "suno", "cloudfront"]):
            print(f"[STREAM REQ] {r.resource_type}: {r.url[:120]}")
    driver.page.on("request", on_req)

    # Hover over the first cover image
    print("[*] Hovering over song cover...")
    cover = driver.page.locator("img[src*='image_c861bf72']").first
    if cover.count() > 0:
        cover.hover()
        time.sleep(1)
        driver.page.screenshot(path="cover_hover.png")
        print("[*] Clicking cover to play...")
        cover.click()
        time.sleep(4)
        
    driver.page.screenshot(path="after_cover_click.png")
    
    # Inspect audio elements and audio context
    audio_info = driver.page.evaluate('''() => {
        const audios = Array.from(document.querySelectorAll('audio')).map(a => ({
            src: a.src,
            currentSrc: a.currentSrc,
            paused: a.paused,
            currentTime: a.currentTime,
            duration: a.duration
        }));
        return audios;
    }''')
    print("[*] Audio elements state:", json.dumps(audio_info, indent=2))

finally:
    driver.quit()

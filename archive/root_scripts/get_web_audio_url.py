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
    
    # Intercept all media requests
    media_requests = []
    def on_req(r):
        if any(ext in r.url for ext in ['.mp3', '.m4a', '.wav', '.ogg', 'audiopipe', 'stream', 'audio']):
            print(f"[MEDIA REQUEST] {r.method} {r.url}")
            media_requests.append(r.url)
    driver.page.on("request", on_req)

    driver.page.screenshot(path="workspace_songs.png")
    
    # Click play button on the first track
    print("[*] Looking for play button or song row...")
    play_btn = driver.page.locator("button[aria-label*='Play' i], button[title*='Play' i], [data-testid*='play' i]").first
    if play_btn.count() > 0:
        print("[*] Clicking play button...")
        play_btn.click()
        time.sleep(4)
    else:
        # Click on the song row
        row = driver.page.locator("text='Ƶʹ', text='极光', [class*='song-row'], [class*='track']").first
        if row.count() > 0:
            print("[*] Clicking song row...")
            row.click()
            time.sleep(4)
            
    # Inspect audio elements
    audio_info = driver.page.evaluate('''() => {
        const audios = Array.from(document.querySelectorAll('audio')).map(a => ({
            src: a.src,
            currentSrc: a.currentSrc,
            paused: a.paused,
            duration: a.duration
        }));
        return audios;
    }''')
    print("[*] Audio elements:", json.dumps(audio_info, indent=2))
    print("[*] Media requests intercepted:", media_requests)
    driver.page.screenshot(path="after_play_clicked.png")

finally:
    driver.quit()

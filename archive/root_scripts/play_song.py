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
    intercepted_audio_requests = []
    def on_req(r):
        if any(x in r.url for x in [".mp3", ".m4a", ".wav", ".ogg", "audiopipe", "stream", "clip", "media"]):
            print(f"[MEDIA REQUEST] {r.method} {r.url}")
            intercepted_audio_requests.append({"url": r.url, "headers": dict(r.headers)})
            
    driver.page.on("request", on_req)

    print("[*] Clicking parent button of cover image via DOM dispatch...")
    driver.page.evaluate("""() => {
        const img = document.querySelector("img[src*='image_c861bf72']");
        if (img) {
            const btn = img.closest('button') || img.parentElement;
            btn.click();
        }
    }""")
    time.sleep(6)
    
    driver.page.screenshot(path="after_click_play_svg.png")
    
    audio_info = driver.page.evaluate('''() => {
        const audios = Array.from(document.querySelectorAll('audio')).map(a => ({
            src: a.src,
            currentSrc: a.currentSrc,
            paused: a.paused,
            currentTime: a.currentTime,
            duration: a.duration
        }));
        return {
            audios,
            footerCurrentTitle: document.querySelector('[class*="playback"], [class*="footer"], [class*="player"]')?.innerText
        };
    }''')
    print("[*] Player state:", json.dumps(audio_info, indent=2, ensure_ascii=False))
    print("[*] Intercepted Audio Requests:", json.dumps(intercepted_audio_requests, indent=2))

finally:
    driver.quit()

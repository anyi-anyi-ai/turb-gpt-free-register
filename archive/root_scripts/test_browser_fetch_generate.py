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
    driver.get("https://suno.com/")
    time.sleep(2)
    driver.page.context.add_cookies([{'name': k, 'value': v, 'url': 'https://suno.com'} for k, v in cookies_dict.items()])
    driver.get("https://suno.com/create")
    time.sleep(6)
    
    # Try calling generate directly from within the browser page
    result = driver.page.evaluate(r"""async (jwtToken) => {
        try {
            const payload = {
                "prompt": "[00:00 Intro]\n极光划破了 沉默的冰原\n像你眼底 闪烁的星弦\n航向未知的 孤独飞行船\n在失重里 寻找着光年\n\n[00:30 Chorus]\n就在这 绚烂的星穹之巅！\n折射出 你我重逢的象限！",
                "tags": "ethereal ambient piano ballad, celestial cinematic orchestral, airy dreamy female vocals, 75 bpm, C major",
                "title": "极光变奏：星穹的极昼 (Part 1)",
                "make_instrumental": false,
                "mv": "chirp-v4",
                "continue_at": null,
                "continue_clip_id": null
            };
            
            const headers = {
                "Content-Type": "application/json",
                "Authorization": "Bearer " + jwtToken
            };
            
            const res = await fetch("https://studio-api.prod.suno.com/api/generate/v2/", {
                method: "POST",
                headers: headers,
                body: JSON.stringify(payload),
                credentials: "include"
            });
            
            const data = await res.json();
            return {
                status: res.status,
                data: data
            };
        } catch(e) {
            return { error: String(e) };
        }
    }""", jwt)
    
    print("[*] Browser Fetch Generate Result:")
    print(json.dumps(result, indent=2, ensure_ascii=False))

finally:
    driver.quit()

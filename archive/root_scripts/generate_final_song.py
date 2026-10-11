import sqlite3
import json
import time
import os
import requests
from core.cloakbrowser_driver import build_cloak_driver

conn = sqlite3.connect('f:/github/turb-gpt-free-register/turb.sqlite3')
cur = conn.cursor()
cur.execute('SELECT email, session_jwt, cookies_json FROM suno_accounts WHERE email="tanxiulan23426@outlook.com"')
row = cur.fetchone()
email, jwt, cookies_json = row
cookies_dict = json.loads(cookies_json)

driver, _ = build_cloak_driver(proxy="http://127.0.0.1:7890", headless=True)

try:
    print("[*] Navigating to suno.com...")
    driver.get("https://suno.com/")
    time.sleep(2)
    
    cookie_list = [{"name": k, "value": v, "url": "https://suno.com"} for k, v in cookies_dict.items()]
    driver.page.context.add_cookies(cookie_list)

    intercepted_requests = []
    intercepted_responses = []

    def on_request(request):
        if "studio-api" in request.url or "generate" in request.url:
            print(f"\n>>> [REQUEST] {request.method} {request.url}")
            try:
                pd = request.post_data
                if pd:
                    print(f">>> Post Data: {pd}")
                intercepted_requests.append({
                    "url": request.url,
                    "method": request.method,
                    "headers": dict(request.headers),
                    "post_data": pd
                })
            except Exception:
                pass

    def on_response(response):
        if "studio-api" in response.url or "feed" in response.url or "generate" in response.url:
            print(f"\n<<< [RESPONSE] {response.status} {response.url}")
            try:
                body = response.text()
                print(f"<<< Body: {body[:300]}")
                intercepted_responses.append({
                    "url": response.url,
                    "status": response.status,
                    "body": body
                })
            except Exception:
                pass

    driver.page.on("request", on_request)
    driver.page.on("response", on_response)

    print("[*] Navigating to suno.com/create...")
    driver.get("https://suno.com/create")
    time.sleep(5)
    
    # Ensure Simple mode
    simple_tab = driver.page.locator("button:has-text('Simple')").first
    if simple_tab.count() > 0:
        simple_tab.click()
        time.sleep(1)
        
    prompt_content = (
        "《极光变奏：星穹的极昼》空灵仙气女声，清脆钢琴碎音，大气宏阔管弦交响渐强，电影原声质感，C大调，75 BPM。\n\n"
        "[00:00 Intro 钢琴碎音，如星光闪烁]\n"
        "(Ah~ Oh~)\n\n"
        "[00:20 Verse 主歌]\n"
        "极光划破了 沉默的冰原\n"
        "像你眼底 闪烁的星弦\n"
        "航向未知的 孤独飞行船\n"
        "在失重里 寻找着光年\n\n"
        "[00:50 Chorus 副歌]\n"
        "就在这 绚烂的星穹之巅！\n"
        "折射出 你我重逢的象限！\n"
        "不需要 氧气和引力成全！\n"
        "频率共振 就是永远！"
    )

    print("[*] Setting prompt via React native setter...")
    driver.page.evaluate(r"""(text) => {
        const ta = document.querySelector('textarea');
        if (ta) {
            const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, "value").set;
            setter.call(ta, text);
            ta.dispatchEvent(new Event('input', { bubbles: true }));
            ta.dispatchEvent(new Event('change', { bubbles: true }));
        }
    }""", prompt_content)
    time.sleep(1.5)
    
    print("[*] Clicking Create button...")
    driver.page.evaluate(r"""() => {
        const btn = Array.from(document.querySelectorAll('button')).find(b => b.textContent.trim().includes('Create'));
        if (btn) btn.click();
    }""")
    
    time.sleep(5)
    driver.page.screenshot(path="after_click_create_real.png")
    
    # Poll for completed clips
    completed_clips = []
    print("[*] Polling feed for completed audio (up to 90s)...")
    for poll in range(25):
        time.sleep(4)
        print(f"[*] Poll {poll+1}/25 ({(poll+1)*4}s elapsed)...")
        
        # Check intercepted feed responses
        for resp in reversed(intercepted_responses):
            if "feed" in resp.get("url", ""):
                try:
                    data = json.loads(resp.get("body", "{}"))
                    clips = data.get("clips", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
                    for c in clips:
                        if c.get("status") == "complete" and c.get("audio_url"):
                            if not any(x["id"] == c["id"] for x in completed_clips):
                                completed_clips.append(c)
                                print(f"[🎉 COMPLETED CLIP] ID: {c.get('id')} | Title: {c.get('title')} | Audio: {c.get('audio_url')}")
                except Exception:
                    pass
                    
        if len(completed_clips) >= 2:
            print("[*] Both clips successfully generated!")
            break
            
    driver.page.screenshot(path="final_feed_result.png")
    
    # Save network log
    with open("final_generation_log.json", "w", encoding="utf-8") as f:
        json.dump({
            "requests": intercepted_requests,
            "responses": intercepted_responses,
            "completed_clips": completed_clips
        }, f, indent=2, ensure_ascii=False)
        
    # Download audio files locally
    os.makedirs("generated_music", exist_ok=True)
    for idx, clip in enumerate(completed_clips):
        audio_url = clip.get("audio_url")
        if audio_url:
            fn = f"generated_music/aurora_variation_clip_{idx+1}_{clip.get('id')[:8]}.mp3"
            print(f"[*] Downloading {audio_url} to {fn}...")
            try:
                r = requests.get(audio_url, timeout=30)
                if r.status_code == 200:
                    with open(fn, "wb") as f:
                        f.write(r.content)
                    print(f"[✅] Saved {fn} ({len(r.content)} bytes)")
            except Exception as e:
                print(f"Error downloading audio: {e}")

finally:
    driver.quit()

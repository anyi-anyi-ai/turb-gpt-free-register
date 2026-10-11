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
    print("[*] Navigating to suno.com...")
    driver.get("https://suno.com/")
    time.sleep(2)
    
    cookie_list = [{"name": k, "value": v, "url": "https://suno.com"} for k, v in cookies_dict.items()]
    driver.page.context.add_cookies(cookie_list)

    # Intercept network requests
    generation_payloads = []
    feed_responses = []

    def on_request(request):
        if "generate" in request.url:
            print(f"\n>>> [REQUEST] {request.method} {request.url}")
            try:
                pd = request.post_data
                print(f">>> Post Data: {pd}")
                generation_payloads.append({
                    "url": request.url,
                    "method": request.method,
                    "headers": dict(request.headers),
                    "post_data": pd
                })
            except Exception as e:
                print(f"Error reading post_data: {e}")

    def on_response(response):
        if "generate" in response.url or "feed" in response.url:
            print(f"\n<<< [RESPONSE] {response.status} {response.url}")
            try:
                body = response.text()
                print(f"<<< Body: {body[:300]}")
                feed_responses.append({
                    "url": response.url,
                    "status": response.status,
                    "body": body
                })
            except Exception as e:
                pass

    driver.page.on("request", on_request)
    driver.page.on("response", on_response)

    print("[*] Navigating to suno.com/create...")
    driver.get("https://suno.com/create")
    time.sleep(5)
    
    # Switch to Simple mode
    print("[*] Ensuring Simple mode...")
    simple_tab = driver.page.locator("button:has-text('Simple')").first
    if simple_tab.count() > 0:
        simple_tab.click()
        time.sleep(1)
        
    prompt_content = (
        "《极光变奏：星穹的极昼》空灵仙气女声，清脆钢琴碎音变奏，大气宏阔管弦乐交响渐强，电影原声质感，C大调，75 BPM。\n\n"
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

    # Set prompt into textarea using DOM event dispatch to avoid any overlay collision
    print("[*] Setting prompt into textarea via DOM event dispatch...")
    driver.page.evaluate(r"""(text) => {
        const ta = document.querySelector('textarea');
        if (ta) {
            ta.value = text;
            ta.dispatchEvent(new Event('input', { bubbles: true }));
            ta.dispatchEvent(new Event('change', { bubbles: true }));
        }
    }""", prompt_content)
    time.sleep(2)
    driver.page.screenshot(path="prompt_ready.png")
    
    create_btn = driver.page.locator("button:has-text('Create')").first
    print("[*] Create button text:", create_btn.text_content(), "disabled:", create_btn.get_attribute("disabled"))
    
    print("[*] Clicking Create!")
    # Click via page locator or dispatch
    driver.page.evaluate(r"""() => {
        const btn = Array.from(document.querySelectorAll('button')).find(b => b.textContent.trim() === 'Create');
        if (btn) btn.click();
    }""")
    time.sleep(5)
    driver.page.screenshot(path="after_create_clicked.png")
    
    # Poll for 60 seconds
    print("[*] Polling generation progress for 60 seconds...")
    for poll in range(15):
        time.sleep(4)
        print(f"[*] Poll {poll+1}/15 ({(poll+1)*4}s)...")
        # Check if songs appeared in UI
        songs = driver.page.evaluate("""() => {
            const items = Array.from(document.querySelectorAll('[data-testid*="cell"], [class*="track"], [class*="song"], tr')).map(e => e.innerText).filter(t => t && t.includes('极光'));
            return items;
        }""")
        if songs:
            print("[*] Found song items in DOM:", songs[:2])
            
    driver.page.screenshot(path="final_generation_state.png")
    
    with open("generation_network_log.json", "w", encoding="utf-8") as f:
        json.dump({
            "requests": generation_payloads,
            "responses": feed_responses
        }, f, indent=2, ensure_ascii=False)
    print("[*] Network log saved to generation_network_log.json")

finally:
    driver.quit()

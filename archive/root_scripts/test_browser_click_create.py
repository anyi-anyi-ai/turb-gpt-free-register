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
    intercepted_requests = []
    intercepted_responses = []

    def on_request(request):
        if "generate" in request.url:
            print(f"\n[REQUEST INTERCEPTED] URL: {request.url}")
            print(f"Method: {request.method}")
            print(f"Headers: {json.dumps(dict(request.headers), indent=2)}")
            try:
                pd = request.post_data
                print(f"Post Data: {pd}")
                intercepted_requests.append({"url": request.url, "headers": dict(request.headers), "post_data": pd})
            except Exception:
                pass

    def on_response(response):
        if "generate" in response.url or "feed" in response.url:
            print(f"\n[RESPONSE INTERCEPTED] URL: {response.url} Status: {response.status}")
            try:
                body = response.text()
                print(f"Response Body: {body[:300]}")
                intercepted_responses.append({"url": response.url, "status": response.status, "body": body})
            except Exception:
                pass

    driver.page.on("request", on_request)
    driver.page.on("response", on_response)

    print("[*] Navigating to suno.com/create...")
    driver.get("https://suno.com/create")
    time.sleep(6)
    
    # Click Simple mode to make it easy
    print("[*] Clicking Simple mode tab...")
    simple_tab = driver.page.locator("button:has-text('Simple')").first
    if simple_tab.count() > 0:
        simple_tab.click()
        time.sleep(1)
        
    # Find textarea with placeholder 'Describe the sound you want'
    prompt_text = "极光变奏：星穹的极昼。空灵仙气女声，清脆钢琴碎音变奏，大气宏阔管弦乐交响渐强，电影原声质感，C大调，75 BPM。\n\n极光划破了 沉默的冰原\n像你眼底 闪烁的星弦\n航向未知的 孤独飞行船\n在失重里 寻找着光年\n\n就在这 绚烂的星穹之巅！\n折射出 你我重逢的象限！\n不需要 氧气和引力成全！\n频率共振 就是永远！"
    
    print("[*] Filling prompt textarea...")
    textarea = driver.page.locator("textarea[placeholder*='Describe the sound']").first
    textarea.click()
    time.sleep(0.5)
    textarea.fill(prompt_text)
    time.sleep(1)
    
    driver.page.screenshot(path="filled_prompt.png")
    
    # Check Create button
    create_btn = driver.page.locator("button:has-text('Create')").first
    print("[*] Create button text:", create_btn.text_content(), "disabled:", create_btn.get_attribute("disabled"))
    
    # Click Create!
    print("[*] Clicking Create button...")
    create_btn.click()
    time.sleep(5)
    
    driver.page.screenshot(path="after_click_create.png")
    
    # Wait and poll for 60 seconds to see generated song
    print("[*] Waiting for generation feed...")
    for i in range(15):
        time.sleep(4)
        print(f"[*] Poll {i+1} at {i*4}s...")
        # Check songs list in UI
        songs = driver.page.evaluate("""() => {
            const songEls = Array.from(document.querySelectorAll('[data-testid*="song"], [class*="song-row"], [class*="track"]'));
            return songEls.map(s => s.innerText);
        }""")
        if songs:
            print("[*] Songs found in UI:", songs)
        if intercepted_responses:
            print("[*] Intercepted responses count:", len(intercepted_responses))
            
    driver.page.screenshot(path="generation_finished.png")
    
    with open("intercepted_data.json", "w", encoding="utf-8") as f:
        json.dump({
            "requests": intercepted_requests,
            "responses": intercepted_responses
        }, f, indent=2, ensure_ascii=False)
        
    print("[*] Data dumped to intercepted_data.json")

finally:
    driver.quit()

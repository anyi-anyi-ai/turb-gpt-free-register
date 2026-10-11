import time
import json
import sqlite3
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
    
    # Inject cookies
    cookie_list = []
    for k, v in cookies_dict.items():
        cookie_list.append({
            "name": k,
            "value": v,
            "url": "https://suno.com",
        })
    driver.page.context.add_cookies(cookie_list)

    print("[*] Navigating to suno.com/onboarding...")
    driver.get("https://suno.com/onboarding")
    
    for i in range(15):
        time.sleep(2)
        url = driver.page.url
        print(f"[*] [{i+1}] Current URL: {url}")
        buttons = driver.page.locator("button").all_text_contents()
        if buttons:
            print(f"[*] Buttons found: {buttons}")
            break
            
    driver.page.screenshot(path="onboarding_settled.png")
    
    # Check if there are any modal or questions or "Get Started" or "Skip"
    body_text = driver.page.evaluate("() => document.body.innerText")
    print("[*] Body text snippet:\n", body_text[:500])

finally:
    driver.quit()

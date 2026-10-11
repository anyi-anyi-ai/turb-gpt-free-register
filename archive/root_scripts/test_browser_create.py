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
    
    # Intercept network requests
    def on_request(request):
        if "generate" in request.url:
            print(f"\n[REQUEST INTERCEPTED] URL: {request.url}")
            print(f"Method: {request.method}")
            print(f"Headers: {json.dumps(dict(request.headers), indent=2)}")
            try:
                print(f"Post Data: {request.post_data}")
            except Exception:
                pass

    def on_response(response):
        if "generate" in response.url:
            print(f"\n[RESPONSE INTERCEPTED] URL: {response.url} Status: {response.status}")
            try:
                print(f"Response Body: {response.text()[:500]}")
            except Exception:
                pass

    driver.page.on("request", on_request)
    driver.page.on("response", on_response)

    print("[*] Navigating to suno.com/create...")
    driver.get("https://suno.com/create")
    time.sleep(8)
    driver.page.screenshot(path="suno_create_page.png")
    
    # Inspect Create page
    page_info = driver.page.evaluate("""() => {
        return {
            url: window.location.href,
            buttons: Array.from(document.querySelectorAll('button')).map(b => b.innerText || b.textContent),
            inputs: Array.from(document.querySelectorAll('textarea, input')).map(i => ({
                tag: i.tagName,
                placeholder: i.placeholder,
                value: i.value
            }))
        };
    }""")
    print("[*] Page info:", json.dumps(page_info, indent=2, ensure_ascii=False))

finally:
    driver.quit()

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
    time.sleep(5)
    
    for step in range(5):
        url = driver.page.url
        print(f"[*] Step {step+1}, URL: {url}")
        if "onboarding" not in url:
            print("[*] Successfully completed onboarding!")
            break
            
        # Try Skip button first, then Continue
        skip_btn = driver.page.locator("button:has-text('Skip')").first
        cont_btn = driver.page.locator("button:has-text('Continue')").first
        
        if skip_btn.count() > 0 and skip_btn.is_visible():
            print("[*] Clicking Skip...")
            skip_btn.click()
        elif cont_btn.count() > 0 and cont_btn.is_visible():
            print("[*] Clicking Continue...")
            cont_btn.click()
        else:
            print("[*] No skip or continue button visible, waiting...")
            
        time.sleep(4)
        driver.page.screenshot(path=f"onboarding_step_{step+1}.png")
        
    print("[*] Navigating to suno.com/create...")
    driver.get("https://suno.com/create")
    time.sleep(6)
    driver.page.screenshot(path="create_page_after_onboarding.png")
    print("[*] Final URL:", driver.page.url)
    
    # Capture fresh cookies & session JWT
    all_cookies = driver.page.context.cookies()
    cookie_map = {c["name"]: c["value"] for c in all_cookies}
    session_jwt = cookie_map.get("__session", "")
    clerk_id = cookie_map.get("__client", "")
    print("[*] __session length:", len(session_jwt))
    print("[*] __client length:", len(clerk_id))
    
    # Check studio-api billing
    billing = driver.page.evaluate(r"""async () => {
        try {
            const resp = await fetch("https://studio-api.prod.suno.com/api/billing/info/", {
                credentials: "include"
            });
            return await resp.json();
        } catch(e) {
            return { error: String(e) };
        }
    }""")
    print("[*] Billing info:", billing)
    
    credits = billing.get("total_credits_left", 300) if isinstance(billing, dict) else 300
    
    # Filter cookies
    essential_cookies = {}
    for k, v in cookie_map.items():
        if k.startswith("__client") or k.startswith("__session") or "suno" in k.lower() or "clerk" in k.lower() or "cf_" in k.lower():
            essential_cookies[k] = v
            
    from core.suno2api_sync import save_suno_account_to_db, push_account_to_suno2api
    save_suno_account_to_db(
        email=email,
        cookies=essential_cookies,
        clerk_client_id=clerk_id,
        session_jwt=session_jwt,
        proxy_used="http://127.0.0.1:7890",
        extra_payload={"billing": billing, "credits": credits}
    )
    push_account_to_suno2api(
        email=email,
        cookies=essential_cookies,
        session_jwt=session_jwt,
        clerk_client_id=clerk_id,
        label=f"{email} (Credits: {credits})"
    )
    print("[*] Pushed fully onboarded account to music2api gateway!")

finally:
    driver.quit()

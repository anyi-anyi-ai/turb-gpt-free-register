import time
import json
from core.cloakbrowser_driver import build_cloak_driver
from core.cloudflare_turnstile import solve_or_wait_cloudflare_turnstile

driver, _ = build_cloak_driver(proxy="http://127.0.0.1:7890", headless=True)
email = "tanxiulan23426@outlook.com"
pwd = "WacoemCbzEakX5jF"

try:
    print("[*] Logging in...")
    driver.get("https://suno.com/sign-up")
    time.sleep(3)
    driver.page.locator("button:has-text('Continue with Microsoft')").first.click()
    
    driver.page.wait_for_selector("input[name='loginfmt']", timeout=20000)
    driver.page.locator("input[name='loginfmt']").fill(email)
    time.sleep(1)
    driver.page.locator("input[name='loginfmt']").press("Enter")
    time.sleep(5)
    
    use_pwd = driver.page.locator("text='Use your password'")
    if use_pwd.count() > 0 and use_pwd.is_visible():
        use_pwd.click()
        time.sleep(3)
    pwd_inp = driver.page.locator("input[name='passwd']").first
    if pwd_inp.count() > 0:
        pwd_inp.fill(pwd)
        time.sleep(1)
        pwd_inp.press("Enter")
        time.sleep(5)
        
    btn = driver.page.locator("button[data-testid='primaryButton'], button:has-text('Next')").first
    if btn.count() > 0 and btn.is_visible():
        btn.click()
        time.sleep(5)
        
    for _ in range(5):
        if "suno.com" in driver.page.url:
            break
        act_btn = driver.page.locator("button[data-testid='primaryButton'], input[value*='Accept' i], input[value*='Yes' i], button:has-text('Accept'), button:has-text('Yes'), #idSIButton9").first
        if act_btn.count() > 0 and act_btn.is_visible():
            act_btn.click()
            time.sleep(5)
        else:
            time.sleep(2)
            
    if "auth/verify" in driver.page.url:
        solve_or_wait_cloudflare_turnstile(driver, timeout=25.0)
        
    for _ in range(15):
        time.sleep(1)
        if "auth/birthday" in driver.page.url or "suno.com/create" in driver.page.url:
            break
            
    print("[*] Landed on birthday page:", driver.page.url)
    combos = driver.page.locator("input[role='combobox']")
    if combos.count() >= 3:
        print("[*] Typing Month...")
        combos.nth(0).click()
        time.sleep(0.5)
        driver.page.keyboard.type("January")
        time.sleep(0.5)
        driver.page.keyboard.press("Enter")
        time.sleep(1.0)
        
        print("[*] Typing Day...")
        combos.nth(1).click()
        time.sleep(0.5)
        driver.page.keyboard.type("15")
        time.sleep(0.5)
        driver.page.keyboard.press("Enter")
        time.sleep(1.0)
        
        print("[*] Typing Year...")
        combos.nth(2).click()
        time.sleep(0.5)
        driver.page.keyboard.type("1998")
        time.sleep(0.5)
        driver.page.keyboard.press("Enter")
        time.sleep(1.0)
        
        driver.page.screenshot(path="bday_keyboard_done.png")
        print("[*] Clicking Continue...")
        driver.page.locator("button:has-text('Continue')").first.click()
        time.sleep(8)
        
    for _ in range(15):
        time.sleep(1)
        if "auth/birthday" not in driver.page.url and "auth/verify" not in driver.page.url:
            break
            
    print("[*] Post-birthday URL:", driver.page.url)
    driver.page.screenshot(path="post_bday_url.png")
    
    # Query billing credits
    billing = driver.page.evaluate(r"""async () => {
        try {
            const resp = await fetch("https://studio-api.prod.suno.com/api/billing/info/");
            return await resp.json();
        } catch(e) {
            return { error: String(e) };
        }
    }""")
    print("[*] Post-birthday billing info:", billing)
    
    all_cookies = driver.page.context.cookies()
    cookie_map = {c["name"]: c["value"] for c in all_cookies}
    print("[*] Total cookies count:", len(all_cookies))
    print("[*] __client:", bool(cookie_map.get("__client")))
    print("[*] __session:", bool(cookie_map.get("__session")))
    
    if billing.get("total_credits_left") is not None or cookie_map.get("__session"):
        from core.suno2api_sync import save_suno_account_to_db, push_account_to_suno2api
        save_suno_account_to_db(
            email=email,
            cookies=cookie_map,
            clerk_client_id=cookie_map.get("__client", ""),
            session_jwt=cookie_map.get("__session", ""),
            proxy_used="http://127.0.0.1:7890",
            extra_payload={
                "billing": billing,
                "credits": billing.get("total_credits_left", 50)
            }
        )
        push_account_to_suno2api(
            email=email,
            cookies=cookie_map,
            session_jwt=cookie_map.get("__session", ""),
            clerk_client_id=cookie_map.get("__client", ""),
            credits=billing.get("total_credits_left", 50)
        )
        print("[*] Successfully pushed finalized active account with credits to gateway!")

finally:
    driver.quit()

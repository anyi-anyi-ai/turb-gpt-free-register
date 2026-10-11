import time
import json
from core.cloakbrowser_driver import build_cloak_driver
from core.cloudflare_turnstile import solve_or_wait_cloudflare_turnstile

driver, _ = build_cloak_driver(proxy="http://127.0.0.1:7890", headless=True)
email = "tanxiulan23426@outlook.com"
pwd = "WacoemCbzEakX5jF"

try:
    print("[*] Logging in with Microsoft OAuth...", flush=True)
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
        
    print("[*] Waiting for birthday prompt...", flush=True)
    driver.page.wait_for_selector("text=When's your birthday?", timeout=30000)
    print("[*] Found birthday prompt!", flush=True)
    
    # Fill Month
    print("[*] Selecting Month...", flush=True)
    month_input = driver.page.locator("input[placeholder='Month']").first
    month_input.click()
    time.sleep(0.5)
    month_input.fill("January")
    time.sleep(0.5)
    driver.page.keyboard.press("Enter")
    time.sleep(0.5)
    
    # Fill Day
    print("[*] Selecting Day...", flush=True)
    day_input = driver.page.locator("input[placeholder='Day']").first
    day_input.click()
    time.sleep(0.5)
    day_input.fill("15")
    time.sleep(0.5)
    driver.page.keyboard.press("Enter")
    time.sleep(0.5)
    
    # Fill Year
    print("[*] Selecting Year...", flush=True)
    year_input = driver.page.locator("input[placeholder='Year']").first
    year_input.click()
    time.sleep(0.5)
    year_input.fill("1998")
    time.sleep(0.5)
    driver.page.keyboard.press("Enter")
    time.sleep(1.0)
    
    driver.page.screenshot(path="bday_filled.png")
    
    # Check if Continue button is enabled
    cont_btn = driver.page.locator("button:has-text('Continue')").first
    print("[*] Continue button disabled?", cont_btn.get_attribute("disabled"), flush=True)
    cont_btn.click()
    print("[*] Clicked Continue, waiting for redirect...", flush=True)
    
    time.sleep(8)
    driver.page.screenshot(path="after_continue.png")
    print("[*] Current URL after continue:", driver.page.url, flush=True)
    
    # Wait for completion of auth / redirect to create or home
    for i in range(15):
        time.sleep(2)
        cookies = {c["name"]: c["value"] for c in driver.page.context.cookies()}
        if cookies.get("__session"):
            print(f"[*] Found __session token at poll {i+1}!", flush=True)
            break
            
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
    print("[*] Final Billing Info:", billing, flush=True)
    
    all_cookies = driver.page.context.cookies()
    cookie_map = {c["name"]: c["value"] for c in all_cookies}
    print("[*] __client present:", bool(cookie_map.get("__client")), flush=True)
    print("[*] __session present:", bool(cookie_map.get("__session")), flush=True)
    
    session_jwt = cookie_map.get("__session", "")
    clerk_id = cookie_map.get("__client", "")
    credits = billing.get("total_credits_left", 50) if isinstance(billing, dict) else 50
    
    from core.suno2api_sync import save_suno_account_to_db, push_account_to_suno2api
    save_suno_account_to_db(
        email=email,
        cookies=cookie_map,
        clerk_client_id=clerk_id,
        session_jwt=session_jwt,
        proxy_used="http://127.0.0.1:7890",
        extra_payload={"billing": billing, "credits": credits}
    )
    push_account_to_suno2api(
        email=email,
        cookies=cookie_map,
        session_jwt=session_jwt,
        clerk_client_id=clerk_id,
        credits=credits
    )
    print("[*] Account onboarding complete and pushed to music2api gateway!", flush=True)

finally:
    driver.quit()

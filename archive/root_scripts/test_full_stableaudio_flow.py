import time
import json
import os
from core.cloakbrowser_driver import build_cloak_driver

email = "VanessaPatterson45616GVx@outlook.com"
password = "StableMusic#Pass2026!"

print(f"[*] Starting full continuous Stable Audio registration for: {email}")

driver, _ = build_cloak_driver(proxy='http://127.0.0.1:7890', headless=True)
try:
    # Step 1: Landing
    print("[1] Navigating to https://stableaudio.com/ ...")
    driver.get("https://stableaudio.com/")
    time.sleep(5)
    
    # Step 2: Click Sign up
    print("[2] Clicking 'Sign up'...")
    driver.page.evaluate('''() => {
        const btn = Array.from(document.querySelectorAll('button')).find(b => (b.innerText || '').trim() === 'Sign up');
        if (btn) btn.click();
    }''')
    
    # Wait for Auth0
    print("[3] Waiting for Auth0 signup page...")
    for _ in range(20):
        time.sleep(1)
        if "auth0.com" in driver.page.url:
            print(f"[*] Arrived at Auth0: {driver.page.url}")
            break
            
    time.sleep(3)
    driver.page.screenshot(path="sa_step1_auth0.png")
    
    # Step 3: Fill signup form
    print("[4] Filling email & password...")
    driver.page.evaluate('''(data) => {
        const emailEl = document.querySelector('input#email') || document.querySelector('input[name="email"]');
        const passEl = document.querySelector('input#password') || document.querySelector('input[name="password"]');
        if (emailEl) {
            emailEl.focus();
            emailEl.value = data.email;
            emailEl.dispatchEvent(new Event('input', { bubbles: true }));
            emailEl.dispatchEvent(new Event('change', { bubbles: true }));
        }
        if (passEl) {
            passEl.focus();
            passEl.value = data.password;
            passEl.dispatchEvent(new Event('input', { bubbles: true }));
            passEl.dispatchEvent(new Event('change', { bubbles: true }));
        }
    }''', {"email": email, "password": password})
    time.sleep(1.5)
    driver.page.screenshot(path="sa_step2_filled.png")
    
    # Step 4: Click Continue
    print("[5] Clicking 'Continue'...")
    driver.page.evaluate('''() => {
        const btn = document.querySelector('button[type="submit"]') || 
                    Array.from(document.querySelectorAll('button')).find(b => (b.innerText || '').trim() === 'Continue');
        if (btn) btn.click();
    }''')
    
    # Step 5: Wait for redirect to stableaudio.com
    print("[6] Waiting for redirect to stableaudio.com ...")
    for _ in range(25):
        time.sleep(1)
        if "stableaudio.com" in driver.page.url and "auth0.com" not in driver.page.url:
            print(f"[*] Returned to Stable Audio: {driver.page.url}")
            break
            
    time.sleep(4)
    driver.page.screenshot(path="sa_step3_onboarding.png")
    
    # Step 6: Handle Cookies & Terms Checkbox
    print("[7] Checking terms checkbox and accepting cookies...")
    terms_res = driver.page.evaluate('''() => {
        // Accept cookies
        const cookieBtn = Array.from(document.querySelectorAll('button')).find(b => (b.innerText || '').trim().includes('Accept All Cookies'));
        if (cookieBtn) cookieBtn.click();
        
        // Find checkbox
        const cbs = Array.from(document.querySelectorAll('input[type="checkbox"]'));
        let cbClicked = false;
        if (cbs.length > 0) {
            cbs[0].click();
            cbs[0].checked = true;
            cbs[0].dispatchEvent(new Event('change', { bubbles: true }));
            cbClicked = true;
        } else {
            // Click label or wrapper
            const labels = Array.from(document.querySelectorAll('label, div')).filter(el => (el.innerText || '').includes("I agree to Stability AI's Terms"));
            if (labels.length > 0) {
                labels[0].click();
                cbClicked = true;
            }
        }
        
        return {
            cbFound: cbs.length,
            cbClicked
        };
    }''')
    print("[*] Checkbox action:", json.dumps(terms_res))
    time.sleep(2)
    driver.page.screenshot(path="sa_step4_checked.png")
    
    # Step 7: Click Agree
    print("[8] Clicking 'Agree' button...")
    agree_info = driver.page.evaluate('''() => {
        const agreeBtn = Array.from(document.querySelectorAll('button')).find(b => (b.innerText || '').trim() === 'Agree');
        if (agreeBtn) {
            const wasDisabled = agreeBtn.disabled;
            agreeBtn.click();
            return { found: true, wasDisabled };
        }
        return { found: false };
    }''')
    print("[*] Agree button action:", json.dumps(agree_info))
    time.sleep(6)
    driver.page.screenshot(path="sa_step5_final.png")
    print(f"[*] Post-Agree URL: {driver.page.url}")
    
    # Step 8: Extract tokens from localStorage and context
    auth_data = driver.page.evaluate('''() => {
        const result = {
            localStorage: {},
            sessionStorage: {}
        };
        for (let i = 0; i < localStorage.length; i++) {
            const k = localStorage.key(i);
            result.localStorage[k] = localStorage.getItem(k);
        }
        for (let i = 0; i < sessionStorage.length; i++) {
            const k = sessionStorage.key(i);
            result.sessionStorage[k] = sessionStorage.getItem(k);
        }
        return result;
    }''')
    
    cookies = driver.page.context.cookies()
    print(f"[*] Total cookies: {len(cookies)}")
    
    # Parse Auth0 token from localStorage
    token_item = None
    for k, v in auth_data["localStorage"].items():
        if "auth0" in k.lower() and "openid" in k:
            try:
                token_item = json.loads(v)
            except Exception:
                pass
                
    if token_item:
        body = token_item.get("body", {})
        print("=== SUCCESS: REGISTERED & EXTRACTED AUTH0 TOKENS ===")
        print("Access Token (first 50):", body.get("access_token")[:50] + "...")
        print("Refresh Token:", body.get("refresh_token"))
        print("Token Type:", body.get("token_type"))
        print("Expires In:", body.get("expires_in"))
        print("Audience:", body.get("audience"))
        
        # Save output
        out_payload = {
            "email": email,
            "password": password,
            "access_token": body.get("access_token"),
            "refresh_token": body.get("refresh_token"),
            "token_type": body.get("token_type"),
            "cookies": {c["name"]: c["value"] for c in cookies},
            "credits": 50,
            "status": "active"
        }
        with open("last_registered_stableaudio.json", "w", encoding="utf-8") as out:
            json.dump(out_payload, out, indent=2, ensure_ascii=False)
            
    else:
        print("[!] Warning: Auth0 token not parsed from localStorage keys:", list(auth_data["localStorage"].keys()))

finally:
    driver.quit()

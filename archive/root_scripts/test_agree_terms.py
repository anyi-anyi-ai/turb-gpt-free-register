import time
import json
import sqlite3
from core.cloakbrowser_driver import build_cloak_driver

email = "VeronicaClark27425II@outlook.com"
password = "StableAudio#Pass2026!"

driver, _ = build_cloak_driver(proxy='http://127.0.0.1:7890', headless=True)
try:
    print("[*] Navigating to https://stableaudio.com/ ...")
    driver.get("https://stableaudio.com/")
    time.sleep(5)
    
    # Click Log in (or Sign up)
    print("[*] Clicking 'Log in'...")
    driver.page.evaluate('''() => {
        const btn = Array.from(document.querySelectorAll('button')).find(b => (b.innerText || '').trim() === 'Log in');
        if (btn) btn.click();
    }''')
    
    # Wait for Auth0
    for _ in range(15):
        time.sleep(1)
        if "auth0.com" in driver.page.url:
            break
            
    time.sleep(2)
    print(f"[*] On Auth0 page: {driver.page.url}")
    
    # Fill login form
    driver.page.evaluate('''(data) => {
        const emailEl = document.querySelector('input#username') || document.querySelector('input#email') || document.querySelector('input[name="username"]');
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
    
    # Click Continue
    print("[*] Clicking Continue to log in...")
    driver.page.evaluate('''() => {
        const btn = document.querySelector('button[type="submit"]') || 
                    Array.from(document.querySelectorAll('button')).find(b => (b.innerText || '').trim() === 'Continue');
        if (btn) btn.click();
    }''')
    time.sleep(6)
    
    print(f"[*] URL after login: {driver.page.url}")
    driver.page.screenshot(path="stableaudio_after_login.png")
    
    # Check if we are on terms page: click Accept All Cookies and Agree to Terms
    print("[*] Handling Cookies and Terms agreement...")
    driver.page.evaluate('''() => {
        // 1. Accept cookies
        const cookieBtn = Array.from(document.querySelectorAll('button')).find(b => (b.innerText || '').trim().includes('Accept All Cookies'));
        if (cookieBtn) cookieBtn.click();
        
        // 2. Check terms checkbox
        const checkboxes = Array.from(document.querySelectorAll('input[type="checkbox"]'));
        if (checkboxes.length > 0) {
            checkboxes[0].click();
            checkboxes[0].checked = true;
            checkboxes[0].dispatchEvent(new Event('change', { bubbles: true }));
        }
    }''')
    time.sleep(2)
    driver.page.screenshot(path="stableaudio_terms_checked.png")
    
    # Click Agree
    print("[*] Clicking 'Agree' button...")
    agree_res = driver.page.evaluate('''() => {
        const agreeBtn = Array.from(document.querySelectorAll('button')).find(b => (b.innerText || '').trim() === 'Agree');
        if (agreeBtn) {
            agreeBtn.click();
            return { clicked: true, disabled: agreeBtn.disabled };
        }
        return { clicked: false };
    }''')
    print("[*] Agree button click:", json.dumps(agree_res))
    time.sleep(6)
    
    driver.page.screenshot(path="stableaudio_post_terms.png")
    print(f"[*] Final URL: {driver.page.url}")
    
    # Extract Cookies and Storage tokens
    storage_info = driver.page.evaluate('''() => {
        return {
            title: document.title,
            currentUrl: window.location.href,
            localStorageKeys: Object.keys(localStorage),
            sessionStorageKeys: Object.keys(sessionStorage),
            auth0Token: Object.entries(localStorage).find(([k, v]) => k.includes('auth0') || v.includes('Bearer') || v.includes('access_token'))
        };
    }''')
    print("[*] Storage info:\n", json.dumps(storage_info, indent=2))
    
    cookies = driver.page.context.cookies()
    print(f"[*] Total cookies captured: {len(cookies)}")
    with open("stableaudio_cookies.json", "w", encoding="utf-8") as f:
        json.dump(cookies, f, indent=2)

finally:
    driver.quit()

import time
import json
import sqlite3
from core.cloakbrowser_driver import build_cloak_driver
from core.outlook_client import OutlookAccount, _CONTEXT_CACHE, _cache_key

# Pick an email from email_pool
conn = sqlite3.connect('turb.sqlite3')
cur = conn.cursor()
cur.execute('SELECT email, payload FROM email_pool WHERE email NOT IN (SELECT email FROM suno_accounts) LIMIT 1')
email, payload_str = cur.fetchone()
payload = json.loads(payload_str)
client_id = payload.get("client_id")
refresh_token = payload.get("refresh_token")

account = OutlookAccount(
    email=email,
    password=payload.get("password", ""),
    client_id=client_id,
    refresh_token=refresh_token
)
_CONTEXT_CACHE[_cache_key(email)] = account

print(f"[*] Testing Stable Audio registration for: {email}")

driver, _ = build_cloak_driver(proxy='http://127.0.0.1:7890', headless=True)
try:
    print("[*] Navigating to https://stableaudio.com/ ...")
    driver.get("https://stableaudio.com/")
    time.sleep(5)
    
    # Click Sign up
    print("[*] Clicking 'Sign up' button...")
    driver.page.evaluate('''() => {
        const btn = Array.from(document.querySelectorAll('button')).find(b => (b.innerText || '').trim() === 'Sign up');
        if (btn) btn.click();
    }''')
    
    # Wait for Auth0 URL
    print("[*] Waiting for Auth0 signup page...")
    for _ in range(20):
        time.sleep(1)
        if "auth0.com" in driver.page.url:
            print(f"[*] Landed on Auth0: {driver.page.url}")
            break
            
    time.sleep(3)
    driver.page.screenshot(path="auth0_signup_page.png")
    
    # Fill email and password via robust JS dispatcher
    test_password = "StableAudio#Pass2026!"
    print(f"[*] Filling email: {email} and password...")
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
    }''', {"email": email, "password": test_password})
    time.sleep(2)
    driver.page.screenshot(path="auth0_form_filled.png")
    
    # Intercept all requests
    def on_req(r):
        if any(k in r.url for k in ['auth0', 'stability', 'verify', 'signup', 'login']):
            print(f"[AUTH0 REQ] {r.method} {r.url}")
    driver.page.on("request", on_req)
    
    def on_res(r):
        if any(k in r.url for k in ['auth0', 'stability', 'verify', 'signup', 'login']):
            print(f"[AUTH0 RES] {r.status} {r.url}")
    driver.page.on("response", on_res)
    
    # Click Continue
    print("[*] Clicking Continue button...")
    driver.page.evaluate('''() => {
        const btn = document.querySelector('button[type="submit"]') || 
                    Array.from(document.querySelectorAll('button')).find(b => (b.innerText || '').trim() === 'Continue');
        if (btn) btn.click();
    }''')
    time.sleep(8)
    driver.page.screenshot(path="auth0_after_continue.png")
    
    print(f"[*] URL after continue: {driver.page.url}")
    page_text = driver.page.evaluate('() => document.body.innerText')
    print("[*] Page text after continue:\n", page_text[:600])

finally:
    driver.quit()

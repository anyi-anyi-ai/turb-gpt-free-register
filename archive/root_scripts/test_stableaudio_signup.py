import time
import json
from core.cloakbrowser_driver import build_cloak_driver

driver, _ = build_cloak_driver(proxy='http://127.0.0.1:7890', headless=True)
try:
    print("[*] Navigating to https://stableaudio.com/ ...")
    driver.get("https://stableaudio.com/")
    time.sleep(5)
    
    # Intercept all requests
    driver.page.on("request", lambda r: print(f"[REQ] {r.method} {r.url}") if any(k in r.url for k in ['auth', 'login', 'signup', 'oauth', 'stability']) else None)
    
    # Click Sign up
    print("[*] Clicking 'Sign up' button...")
    driver.page.evaluate('''() => {
        const btn = Array.from(document.querySelectorAll('button')).find(b => (b.innerText || '').trim() === 'Sign up');
        if (btn) btn.click();
    }''')
    time.sleep(6)
    
    driver.page.screenshot(path="stableaudio_after_signup_click.png")
    print("[*] Current URL after Sign up click:", driver.page.url)
    
    # Inspect current page form inputs
    form_info = driver.page.evaluate('''() => {
        const inputs = Array.from(document.querySelectorAll('input')).map(i => ({
            type: i.type,
            name: i.name,
            id: i.id,
            placeholder: i.placeholder,
            ariaLabel: i.getAttribute('aria-label')
        }));
        const buttons = Array.from(document.querySelectorAll('button')).map(b => (b.innerText || '').trim()).filter(Boolean);
        return {
            title: document.title,
            url: window.location.href,
            inputs,
            buttons
        };
    }''')
    print("Form info:\n", json.dumps(form_info, indent=2, ensure_ascii=False))

finally:
    driver.quit()

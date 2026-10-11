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

    print("[*] Navigating to suno.com/create...")
    driver.get("https://suno.com/create")
    time.sleep(5)
    
    # Switch to Simple mode
    print("[*] Ensuring Simple mode...")
    simple_tab = driver.page.locator("button:has-text('Simple')").first
    if simple_tab.count() > 0:
        simple_tab.click()
        time.sleep(1)
        
    prompt_content = "《极光变奏：星穹的极昼》空灵仙气女声，清脆钢琴碎音，大气宏阔交响，75 BPM，C大调"
    
    # Use native React setter + dispatch
    print("[*] Setting value via React native setter...")
    driver.page.evaluate(r"""(text) => {
        const ta = document.querySelector('textarea');
        if (ta) {
            const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, "value").set;
            setter.call(ta, text);
            ta.dispatchEvent(new Event('input', { bubbles: true }));
            ta.dispatchEvent(new Event('change', { bubbles: true }));
        }
    }""", prompt_content)
    time.sleep(1.5)
    
    # Check button disabled status and className
    btn_info = driver.page.evaluate(r"""() => {
        const btn = Array.from(document.querySelectorAll('button')).find(b => b.textContent.trim().includes('Create'));
        return btn ? {
            text: btn.textContent.trim(),
            disabled: btn.disabled,
            ariaDisabled: btn.getAttribute('aria-disabled'),
            className: btn.className
        } : null;
    }""")
    print("[*] Create button info after React setter:", btn_info)
    driver.page.screenshot(path="btn_active_check.png")

finally:
    driver.quit()

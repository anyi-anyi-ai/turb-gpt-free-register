import time
from core.cloakbrowser_driver import build_cloak_driver
from core.cloudflare_turnstile import solve_or_wait_cloudflare_turnstile

driver, _ = build_cloak_driver(proxy="http://127.0.0.1:7890", headless=True)
email = "min1922396@outlook.com"
pwd = "GPybeYNWIJ0uk1dI"

try:
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
            
    print("Current URL:", driver.page.url)
    if "auth/verify" in driver.page.url:
        solve_or_wait_cloudflare_turnstile(driver, timeout=25.0)
        
    for _ in range(15):
        time.sleep(1)
        if "auth/birthday" in driver.page.url:
            break
            
    print("Birthday URL:", driver.page.url)
    time.sleep(2)
    driver.page.screenshot(path="min_bday.png")
    
    # Inspect elements on birthday page
    els = driver.page.evaluate(r"""() => {
        return Array.from(document.querySelectorAll('*')).filter(e => {
            return e.children.length === 0 && (e.innerText || '').trim().length > 0;
        }).map(e => ({ tag: e.tagName, text: e.innerText.trim(), class: e.className }));
    }""")
    for el in els[:30]:
        print(el)
        
finally:
    driver.quit()

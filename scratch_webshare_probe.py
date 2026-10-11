import time
from core.cloakbrowser_driver import build_cloak_driver

driver, _ = build_cloak_driver(
    proxy="http://JP.sess_testsubmit:resin_proxy_c3d7b8e1f0a2496582d9183b@127.0.0.1:2260",
    headless=True,
    account_email="test_submit_probe@outlook.com"
)
page = driver.page

all_resps = []
page.on("response", lambda res: all_resps.append((res.status, res.url, res.request.method)))

try:
    page.goto("https://dashboard.webshare.io/register", timeout=30000, wait_until="domcontentloaded")
    page.wait_for_timeout(2000)
    
    # Fill email & password
    page.locator('input[type="email"]').first.fill("LaurenEstrada817524@outlook.com")
    page.locator('input[type="password"]').first.fill("Abc12345!@#$%^&*")
    
    # Click terms checkbox
    cb = page.locator('input[type="checkbox"]').first
    if cb.count() > 0 and not cb.is_checked():
        cb.click()
    
    # Click Sign Up With Email
    btn = page.locator('button:has-text("Sign Up With Email")').first
    btn.click()
    print("Clicked Sign Up With Email!")
    page.wait_for_timeout(6000)
    
    print("Current URL:", page.url)
    for s, u, m in all_resps:
        if m == "POST" or "api" in u:
            print(f"API: [{m}] {s} -> {u}")
    
    print("Page text summary:", page.inner_text("body")[:400].replace("\n", " | "))
    page.screenshot(path="webshare_after_click.png")
finally:
    driver.quit()

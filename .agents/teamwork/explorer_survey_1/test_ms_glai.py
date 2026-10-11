import sys, time
sys.stdout.reconfigure(encoding='utf-8')
from playwright.sync_api import sync_playwright

proxy = {'server': 'http://127.0.0.1:2260', 'username': 'NL', 'password': 'resin_proxy_c3d7b8e1f0a2496582d9183b'}

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, proxy=proxy, args=["--disable-blink-features=AutomationControlled"])
    ctx = browser.new_context(locale='en-US', viewport={'width': 1366, 'height': 768})
    page = ctx.new_page()
    page.route("**/google-analytics.com/**", lambda r: r.abort())
    page.route("**/googletagmanager.com/**", lambda r: r.abort())

    t0 = time.time()
    school_email = "glai1@alumno.uned.es"
    print(f"Navigating to login.microsoftonline.com for {school_email}...")
    page.goto(f"https://login.microsoftonline.com/?login_hint={school_email}", wait_until="domcontentloaded", timeout=35000)
    page.wait_for_timeout(4000)
    print(f"Current URL: {page.url}")
    print(f"Title: {page.title()}")

    # Check for #i0116 or federation redirect
    if page.locator("#i0116").count() > 0 and page.locator("#i0116").first.is_visible():
        print("At Microsoft login input #i0116, clicking Next...")
        page.locator("#idSIButton9").first.click()
        page.wait_for_timeout(4000)
        print(f"After next URL: {page.url}")

    # Wait for UNED SSO or settings
    page.wait_for_selector("#ContentPlaceHolder1_ssousername, button#settingsId", timeout=30000)
    print("Found UNED SSO selector or settings!")
    print(f"URL: {page.url}")
    if page.locator("#ContentPlaceHolder1_ssousername").count() > 0:
        print("Filling UNED SSO credentials for glai1...")
        page.fill("#ContentPlaceHolder1_ssousername", "glai1")
        page.fill("#ContentPlaceHolder1_password", "Uned@1901!Zhang#88")
        page.click("#ContentPlaceHolder1_Button1")
        page.wait_for_timeout(6000)
        print(f"After SSO click URL: {page.url}")
        print(f"Title: {page.title()}")
        page.screenshot(path=".agents/teamwork/explorer_survey_1/glai_after_sso.png")
    browser.close()

import sys
from playwright.sync_api import sync_playwright

proxy = {'server': 'http://127.0.0.1:2260', 'username': 'NL', 'password': 'resin_proxy_c3d7b8e1f0a2496582d9183b'}

def add_asset_blocking(page):
    page.route("**/*.{woff,woff2,ttf,eot,png,jpg,jpeg,gif,svg,ico}", lambda r: r.abort())
    page.route("**/gtm.js**", lambda r: r.abort())
    page.route("**/google-analytics.com/**", lambda r: r.abort())
    page.route("**/googletagmanager.com/**", lambda r: r.abort())

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, proxy=proxy, args=["--disable-blink-features=AutomationControlled"])
    ctx = browser.new_context(locale='es-ES')
    page = ctx.new_page()
    add_asset_blocking(page)
    try:
        resp = page.goto("https://sso.uned.es/sso/index.aspx?URL=https%3a%2f%2flogin.uned.es%2fssouned%2flogin.jsp", wait_until='commit', timeout=20000)
        page.wait_for_timeout(5000)
        print("Status code:", resp.status if resp else None)
        print("URL:", page.url)
        page.screenshot(path=".agents/teamwork/explorer_survey_1/sso_page.png")
        print("Title:", page.title())
        print("Content length:", len(page.content()))
        print("First 500 chars of body:\n", page.locator("body").inner_text()[:500])
    except Exception as e:
        print("Error:", e)
    browser.close()

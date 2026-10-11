import sys, time
sys.stdout.reconfigure(encoding='utf-8')
from playwright.sync_api import sync_playwright

proxy = {'server': 'http://127.0.0.1:2260', 'username': 'NL', 'password': 'resin_proxy_c3d7b8e1f0a2496582d9183b'}

def add_asset_blocking(page):
    page.route("**/*.{woff,woff2,ttf,eot,png,jpg,jpeg,gif,svg,ico}", lambda r: r.abort())
    page.route("**/gtm.js**", lambda r: r.abort())
    page.route("**/google-analytics.com/**", lambda r: r.abort())
    page.route("**/googletagmanager.com/**", lambda r: r.abort())

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, proxy=proxy)
    ctx = browser.new_context(locale='es-ES', viewport={'width': 1280, 'height': 800})
    page = ctx.new_page()
    add_asset_blocking(page)

    t0 = time.time()
    page.goto('https://sso.uned.es/sso/index.aspx?URL=https%3a%2f%2flogin.uned.es%2fssouned%2flogin.jsp', wait_until='commit', timeout=20000)
    page.wait_for_selector('#ContentPlaceHolder1_ssousername', timeout=12000)
    page.fill('#ContentPlaceHolder1_ssousername', 'jli106')
    page.fill('#ContentPlaceHolder1_password', 'Uned@4893!Zhang#88')
    page.click('#ContentPlaceHolder1_Button1')
    page.wait_for_timeout(4000)
    print(f'Login took {time.time()-t0:.2f}s, URL: {page.url}')
    body = page.locator("body").inner_text()
    print('Body text:', repr(body[:300]))

    if "login.jsp" in page.url or "salida.aspx" in page.url or "serviwebng" in page.url or "sso.uned.es" not in page.url:
        print("SSO Login successful!")
        # check serviwebng
        page.goto('https://serviwebng.uned.es/correo/activar-correo.asp', wait_until='commit', timeout=20000)
        page.wait_for_timeout(3000)
        print('Serviweb URL:', page.url)
        print('Serviweb text:\n', repr(page.locator('body').inner_text()[:300]))
    browser.close()

import sys
sys.stdout.reconfigure(encoding='utf-8')
from playwright.sync_api import sync_playwright

proxy = {'server': 'http://127.0.0.1:2260', 'username': 'NL', 'password': 'resin_proxy_c3d7b8e1f0a2496582d9183b'}

with sync_playwright() as p:
    b = p.chromium.launch(headless=True, proxy=proxy)
    ctx = b.new_context(locale='es-ES')
    page = ctx.new_page()
    page.route('**/*.{woff,woff2,ttf,eot,png,jpg,jpeg,gif,svg,ico}', lambda r: r.abort())
    page.goto('https://sso.uned.es/sso/index.aspx?URL=https%3a%2f%2flogin.uned.es%2fssouned%2flogin.jsp', wait_until='commit')
    page.wait_for_timeout(3000)
    print("Frames count:", len(page.frames))
    for f in page.frames:
        print("Frame name:", f.name, "url:", f.url)
        inps = f.locator("input").all()
        print(f"  Inputs count in frame: {len(inps)}")
        for inp in inps:
            print(f"    id={inp.get_attribute('id')} name={inp.get_attribute('name')}")
    b.close()

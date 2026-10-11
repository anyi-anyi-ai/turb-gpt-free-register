import time
from playwright.sync_api import sync_playwright

print("Launching standard Playwright with channel='chrome'...")
with sync_playwright() as p:
    browser = p.chromium.launch(
        headless=False,
        channel="chrome",
        args=["--start-maximized", "--no-sandbox"],
    )
    context = browser.new_context(no_viewport=True)
    page = context.new_page()
    page.goto("https://www.google.com")
    print("Google opened in Chrome. Sleeping 5 seconds...")
    time.sleep(5)
    browser.close()
print("Done.")

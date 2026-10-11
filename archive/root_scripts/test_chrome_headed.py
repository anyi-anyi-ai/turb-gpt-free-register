import time
from playwright.sync_api import sync_playwright

proxy_settings = {"server": "http://127.0.0.1:7890"}

print("Launching Chrome headed with proxy...")
with sync_playwright() as p:
    browser = p.chromium.launch(
        headless=False,
        channel="chrome",
        proxy=proxy_settings,
        args=["--start-maximized"]
    )
    context = browser.new_context(no_viewport=True)
    page = context.new_page()
    page.goto("https://elevenlabs.io/sign-up")
    print("ElevenLabs opened! Sleeping 6 seconds...")
    time.sleep(6)
    print("Page title:", page.title())
    browser.close()
print("Done.")

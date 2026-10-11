import time
from core.cloakbrowser_driver import build_cloak_driver

d, _ = build_cloak_driver(proxy="http://127.0.0.1:7890", headless=True)
try:
    d.get("https://elevenlabs.io/sign-up")
    time.sleep(5)

    email = "diag_check_puzzle_1@outlook.com"
    pwd = "ElevenTestPass#2026!"

    d.page.locator('input[type="email"]').fill(email)
    time.sleep(0.5)
    d.page.locator('input[type="password"]').fill(pwd)
    time.sleep(1.0)

    print("Pressing Enter...")
    d.page.keyboard.press("Enter")

    for i in range(1, 4):
        time.sleep(5)
        print(f"Waiting {i*5}s...")
        d.page.screenshot(path=f"hcaptcha_step_{i}.png")
        info = d.page.evaluate('''() => {
            const iframes = Array.from(document.querySelectorAll('iframe'));
            const challenge = iframes.find(f => (f.title || '').includes('challenge') || (f.src || '').includes('challenge'));
            return {
                hasChallenge: Boolean(challenge),
                challengeVisible: challenge ? (challenge.offsetWidth > 0 && challenge.offsetHeight > 0) : false,
                text: (document.body && document.body.innerText) ? document.body.innerText.substring(0, 300) : ''
            };
        }''')
        print("Info:", info)
finally:
    d.quit()

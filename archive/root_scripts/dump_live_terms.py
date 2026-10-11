import json
import time
from core.cloakbrowser_driver import build_cloak_driver

driver, _ = build_cloak_driver(proxy='http://127.0.0.1:7890', headless=True)
try:
    with open('stableaudio_cookies.json', encoding='utf-8') as f:
        cookies = json.load(f)
    driver.get('https://stableaudio.com/')
    time.sleep(2)
    driver.page.context.add_cookies(cookies)
    
    # Inject localStorage
    auth0_key = "@@auth0spajs@@::NrmFAn7R9YEtZAKiXjbH5Gd9KHBkQ10x::https://stableaudio.com::openid profile email offline_access"
    with open('test_agree_terms.py') as f:
        pass
    
    with open('stableaudio_session_dump.json', 'w', encoding='utf-8') as f:
        pass

    driver.get('https://stableaudio.com/login')
    time.sleep(3)
    
    # If on auth0 login
    if 'auth0.com' in driver.page.url:
        print("[*] Logging in via Auth0...")
        driver.page.evaluate('''() => {
            const emailEl = document.querySelector('input#username') || document.querySelector('input#email');
            const passEl = document.querySelector('input#password');
            if (emailEl) {
                emailEl.value = 'VeronicaClark27425II@outlook.com';
                emailEl.dispatchEvent(new Event('input', { bubbles: true }));
                emailEl.dispatchEvent(new Event('change', { bubbles: true }));
            }
            if (passEl) {
                passEl.value = 'StableAudio#Pass2026!';
                passEl.dispatchEvent(new Event('input', { bubbles: true }));
                passEl.dispatchEvent(new Event('change', { bubbles: true }));
            }
            const btn = document.querySelector('button[type="submit"]') || Array.from(document.querySelectorAll('button')).find(b => (b.innerText || '').trim() === 'Continue');
            if (btn) btn.click();
        }''')
        time.sleep(6)
        
    print("[*] URL after login/redirect:", driver.page.url)
    driver.page.screenshot(path="terms_live_view.png")
    
    # Detailed inspect of the terms card
    card_details = driver.page.evaluate('''() => {
        const card = document.querySelector('.max-w-\\\\[400px\\\\]') || document.querySelector('[class*=\"max-w-\"]');
        if (!card) return { found: false };
        const allDescendants = Array.from(card.querySelectorAll('*')).map(el => ({
            tag: el.tagName,
            type: el.getAttribute('type'),
            role: el.getAttribute('role'),
            text: el.innerText,
            className: el.className
        }));
        return {
            found: true,
            html: card.innerHTML,
            descendants: allDescendants
        };
    }''')
    
    with open("terms_card_live.json", "w", encoding="utf-8") as f:
        json.dump(card_details, f, indent=2, ensure_ascii=False)
        
    print(f"[*] Live card found: {card_details.get('found')}")

finally:
    driver.quit()

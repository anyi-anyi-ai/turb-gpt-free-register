import json
from core.cloakbrowser_driver import build_cloak_driver
import time

driver, _ = build_cloak_driver(proxy='http://127.0.0.1:7890', headless=True)
try:
    with open('stableaudio_cookies.json', encoding='utf-8') as f:
        cookies = json.load(f)
    driver.get('https://stableaudio.com/')
    time.sleep(2)
    driver.page.context.add_cookies(cookies)
    driver.get('https://stableaudio.com/terms?onboarding=1')
    time.sleep(5)
    
    info = driver.page.evaluate('''() => {
        const inputs = Array.from(document.querySelectorAll('input')).map(i => ({
            type: i.type, id: i.id, name: i.name, checked: i.checked, className: i.className
        }));
        const labels = Array.from(document.querySelectorAll('label')).map(l => ({
            text: l.innerText, for: l.getAttribute('for'), html: l.outerHTML.slice(0, 200)
        }));
        const buttons = Array.from(document.querySelectorAll('button')).map(b => ({
            text: b.innerText, disabled: b.disabled, className: b.className
        }));
        return { inputs, labels, buttons };
    }''')
    print("Terms elements:\n", json.dumps(info, indent=2))
    
    # Try clicking the label or input directly
    print("[*] Clicking label for terms...")
    driver.page.evaluate('''() => {
        const labels = Array.from(document.querySelectorAll('label'));
        if (labels.length > 0) {
            labels[0].click();
        }
        const inputs = Array.from(document.querySelectorAll('input[type="checkbox"]'));
        if (inputs.length > 0) {
            inputs[0].click();
        }
    }''')
    time.sleep(2)
    driver.page.screenshot(path="after_label_click.png")
    
    # Check if Agree button enabled
    btn_state = driver.page.evaluate('''() => {
        const agreeBtn = Array.from(document.querySelectorAll('button')).find(b => (b.innerText || '').trim() === 'Agree');
        if (agreeBtn) {
            return { text: agreeBtn.innerText, disabled: agreeBtn.disabled, className: agreeBtn.className };
        }
        return null;
    }''')
    print("[*] Agree button state after click:", btn_state)
    
    if btn_state and not btn_state.get('disabled'):
        print("[*] Agree is enabled! Clicking Agree...")
        driver.page.evaluate('''() => {
            const agreeBtn = Array.from(document.querySelectorAll('button')).find(b => (b.innerText || '').trim() === 'Agree');
            if (agreeBtn) agreeBtn.click();
        }''')
        time.sleep(5)
        driver.page.screenshot(path="after_agree_final.png")
        print("[*] URL after Agree:", driver.page.url)

finally:
    driver.quit()

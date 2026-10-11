import sqlite3
import json
import time
from core.cloakbrowser_driver import build_cloak_driver

conn = sqlite3.connect('turb.sqlite3')
cur = conn.cursor()
cur.execute('SELECT email, session_jwt, cookies_json FROM suno_accounts WHERE email="tanxiulan23426@outlook.com"')
email, jwt, cookies_json = cur.fetchone()
cookies_dict = json.loads(cookies_json)

driver, _ = build_cloak_driver(proxy='http://127.0.0.1:7890', headless=True)
try:
    driver.get('https://suno.com/')
    time.sleep(2)
    driver.page.context.add_cookies([{'name': k, 'value': v, 'url': 'https://suno.com'} for k, v in cookies_dict.items()])
    driver.get('https://suno.com/create')
    time.sleep(6)
    
    # Click Remix button on the song card
    print("[*] Looking for Remix button...")
    click_remix = driver.page.evaluate('''() => {
        // Find Remix button
        const remixBtn = document.querySelector('button[aria-label="Remix"]');
        if (remixBtn) {
            remixBtn.click();
            return "Clicked aria-label Remix";
        }
        const btns = Array.from(document.querySelectorAll('button')).filter(b => b.innerText && b.innerText.trim() === 'Remix');
        if (btns.length > 0) {
            btns[0].click();
            return "Clicked text Remix";
        }
        return "Remix not found";
    }''')
    print("[*] Remix click result:", click_remix)
    time.sleep(2)
    driver.page.screenshot(path="after_click_remix.png")
    
    # Check what popped up
    popup = driver.page.evaluate('''() => {
        const items = Array.from(document.querySelectorAll('[role="menuitem"], button, div')).filter(el => {
            const txt = (el.innerText || '').trim();
            return ['Extend', 'Cover', 'Reuse Prompt', 'Reuse Style', 'Infill'].includes(txt);
        }).map(el => ({
            text: el.innerText.trim(),
            className: el.className
        }));
        return items;
    }''')
    print("[*] Remix options:", json.dumps(popup, indent=2, ensure_ascii=False))
    
    # Click Extend
    print("[*] Clicking Extend...")
    click_extend = driver.page.evaluate('''() => {
        const ext = Array.from(document.querySelectorAll('*')).find(el => el.innerText && el.innerText.trim() === 'Extend');
        if (ext) {
            ext.click();
            return "Clicked Extend";
        }
        return "Extend not found";
    }''')
    print("[*] Extend click result:", click_extend)
    time.sleep(3)
    driver.page.screenshot(path="after_click_extend.png")
    
    # Check form state
    form_state = driver.page.evaluate('''() => {
        const tas = Array.from(document.querySelectorAll('textarea')).map(t => ({
            placeholder: t.placeholder,
            value: t.value
        }));
        const inputs = Array.from(document.querySelectorAll('input')).map(i => ({
            type: i.type,
            placeholder: i.placeholder,
            value: i.value,
            name: i.name
        }));
        return {
            tas,
            inputs: inputs.filter(i => i.value || i.placeholder)
        };
    }''')
    print("[*] Form state after extend:", json.dumps(form_state, indent=2, ensure_ascii=False))

finally:
    driver.quit()

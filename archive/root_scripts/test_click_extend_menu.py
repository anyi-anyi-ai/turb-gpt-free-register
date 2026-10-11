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
    
    # 1. Click More options on the song
    driver.page.evaluate('''() => {
        const btns = Array.from(document.querySelectorAll('button[aria-label="More options"]'));
        if (btns.length > 0) btns[0].click();
    }''')
    time.sleep(1.5)
    
    # 2. Hover Edit menu item
    driver.page.evaluate('''() => {
        const editItem = Array.from(document.querySelectorAll('[role="menuitem"]')).find(el => (el.innerText || '').trim() === 'Edit');
        if (editItem) {
            editItem.dispatchEvent(new PointerEvent('pointerenter', { bubbles: true }));
            editItem.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));
            editItem.click();
        }
    }''')
    time.sleep(1.5)
    
    # 3. Click Extend!
    print("[*] Clicking 'Extend' menuitem...")
    res = driver.page.evaluate('''() => {
        const ext = Array.from(document.querySelectorAll('[role="menuitem"]')).find(el => (el.innerText || '').trim() === 'Extend');
        if (ext) {
            ext.click();
            return "Clicked Extend successfully!";
        }
        return "Extend not found";
    }''')
    print("[*] Click result:", res)
    time.sleep(4)
    driver.page.screenshot(path="after_extend_menu_click.png")
    
    # 4. Inspect form in Create drawer
    form_info = driver.page.evaluate('''() => {
        const tas = Array.from(document.querySelectorAll('textarea')).map(t => ({
            placeholder: t.placeholder,
            value: t.value,
            className: t.className
        }));
        const inputs = Array.from(document.querySelectorAll('input')).map(i => ({
            type: i.type,
            placeholder: i.placeholder,
            value: i.value,
            ariaLabel: i.getAttribute('aria-label')
        })).filter(i => i.placeholder || i.ariaLabel || i.value);
        
        const labels = Array.from(document.querySelectorAll('label, div, span')).filter(el => {
            const txt = (el.innerText || '').trim();
            return txt.includes('Extend from') || txt.includes('01:') || txt.includes('Extend');
        }).map(el => (el.innerText || '').trim());
        
        const createBtn = document.querySelector('button[class*="gradient"], button:has-text("Create")') || 
                          Array.from(document.querySelectorAll('button')).find(b => b.innerText && b.innerText.includes('Create'));
                          
        return {
            tas,
            inputs,
            labels: Array.from(new Set(labels)).slice(0, 10),
            createBtn: createBtn ? { text: createBtn.innerText, disabled: createBtn.disabled } : null
        };
    }''')
    print("[*] Form details after Extend click:\n", json.dumps(form_info, indent=2, ensure_ascii=False))

finally:
    driver.quit()

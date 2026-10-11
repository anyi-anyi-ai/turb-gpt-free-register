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
    
    # 1. Click Remix on first song
    driver.page.evaluate('''() => {
        const remixBtn = document.querySelector('button[aria-label="Remix"]') || 
                         Array.from(document.querySelectorAll('button')).find(b => b.innerText && b.innerText.trim() === 'Remix');
        if (remixBtn) remixBtn.click();
    }''')
    time.sleep(2)
    
    # 2. Click Advanced
    driver.page.evaluate('''() => {
        const adv = Array.from(document.querySelectorAll('button, div, span')).find(el => (el.innerText || '').trim() === 'Advanced');
        if (adv) adv.click();
    }''')
    time.sleep(2)
    
    # 3. Click the "Cover" dropdown button
    print("[*] Clicking 'Cover' dropdown...")
    driver.page.evaluate('''() => {
        const coverBtn = Array.from(document.querySelectorAll('button')).find(b => (b.innerText || '').trim().includes('Cover'));
        if (coverBtn) coverBtn.click();
    }''')
    time.sleep(1.5)
    driver.page.screenshot(path="after_click_cover_dropdown.png")
    
    # 4. Check dropdown menu options
    menu_items = driver.page.evaluate('''() => {
        const items = Array.from(document.querySelectorAll('[role="menuitem"], [role="option"], button, div')).filter(el => {
            const txt = (el.innerText || '').trim();
            return ['Extend', 'Cover', 'Infill', 'Crop'].includes(txt);
        }).map(el => el.innerText.trim());
        return Array.from(new Set(items));
    }''')
    print("[*] Dropdown options:", menu_items)
    
    # 5. Click "Extend"
    print("[*] Selecting 'Extend'...")
    driver.page.evaluate('''() => {
        const ext = Array.from(document.querySelectorAll('[role="menuitem"], [role="option"], button, div')).find(el => (el.innerText || '').trim() === 'Extend');
        if (ext) ext.click();
    }''')
    time.sleep(2)
    driver.page.screenshot(path="after_selected_extend.png")
    
    # 6. Check inputs in Extend mode
    extend_ui = driver.page.evaluate('''() => {
        const inputs = Array.from(document.querySelectorAll('input')).map(i => ({
            type: i.type,
            placeholder: i.placeholder,
            value: i.value,
            ariaLabel: i.getAttribute('aria-label')
        })).filter(i => i.placeholder || i.ariaLabel || i.value);
        
        const labels = Array.from(document.querySelectorAll('label, span, div')).map(l => (l.innerText || '').trim()).filter(t => t.includes('Extend from') || t.includes('01:28') || t.includes('00:'));
        return { inputs, labels: labels.slice(0, 10) };
    }''')
    print("[*] Extend UI State:", json.dumps(extend_ui, indent=2, ensure_ascii=False))

finally:
    driver.quit()

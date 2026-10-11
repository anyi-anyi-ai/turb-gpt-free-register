import sqlite3
import json
import time
from core.cloakbrowser_driver import build_cloak_driver

conn = sqlite3.connect('turb.sqlite3')
cur = conn.cursor()
cur.execute('SELECT session_jwt, cookies_json FROM suno_accounts LIMIT 1')
jwt, cj = cur.fetchone()
cookies_dict = json.loads(cj)

driver, _ = build_cloak_driver(proxy='http://127.0.0.1:7890', headless=True)
try:
    driver.get('https://suno.com/')
    time.sleep(2)
    driver.page.context.add_cookies([{'name': k, 'value': v, 'url': 'https://suno.com'} for k, v in cookies_dict.items()])
    driver.get('https://suno.com/create')
    time.sleep(6)
    
    # 1. More options
    driver.page.evaluate('''() => {
        const btns = Array.from(document.querySelectorAll('button[aria-label="More options"]'));
        if (btns.length > 0) btns[0].click();
    }''')
    time.sleep(1.5)
    
    # 2. Edit
    driver.page.evaluate('''() => {
        const editItem = Array.from(document.querySelectorAll('[role="menuitem"]')).find(el => (el.innerText || '').trim() === 'Edit');
        if (editItem) {
            editItem.dispatchEvent(new PointerEvent('pointerenter', { bubbles: true }));
            editItem.click();
        }
    }''')
    time.sleep(1.5)
    
    # 3. Extend
    driver.page.evaluate('''() => {
        const ext = Array.from(document.querySelectorAll('[role="menuitem"]')).find(el => (el.innerText || '').trim() === 'Extend');
        if (ext) ext.click();
    }''')
    time.sleep(3)
    
    # 4. Check Select All and Extend from elements
    res = driver.page.evaluate('''() => {
        const selectAllBtn = Array.from(document.querySelectorAll('button')).find(b => (b.innerText || '').trim() === 'Select All');
        const container = selectAllBtn ? selectAllBtn.parentElement : null;
        return {
            containerHTML: container ? container.outerHTML : null,
            buttonsInContainer: container ? Array.from(container.querySelectorAll('button, input, div')).map(el => ({ tag: el.tagName, text: el.innerText, html: el.outerHTML })) : []
        };
    }''')
    print("Container details:", json.dumps(res, indent=2))
    
    # 5. Click Select All and see what happens to the pink bar and text
    print("[*] Clicking Select All...")
    driver.page.evaluate('''() => {
        const selectAllBtn = Array.from(document.querySelectorAll('button')).find(b => (b.innerText || '').trim() === 'Select All');
        if (selectAllBtn) selectAllBtn.click();
    }''')
    time.sleep(2)
    driver.page.screenshot(path="after_click_select_all.png")
    
    # 6. Check state after Select All
    after_select = driver.page.evaluate('''() => {
        const selectAllBtn = Array.from(document.querySelectorAll('button')).find(b => (b.innerText || '').trim() === 'Select All');
        const container = selectAllBtn ? selectAllBtn.parentElement : null;
        return container ? container.innerText : null;
    }''')
    print("[*] Container text after Select All:", after_select)

finally:
    driver.quit()

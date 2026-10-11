import sqlite3
import json
import time
from core.cloakbrowser_driver import build_cloak_driver

conn = sqlite3.connect('turb.sqlite3')
cur = conn.cursor()
cur.execute('SELECT session_jwt, cookies_json FROM suno_accounts LIMIT 1')
jwt, cj = cur.fetchone()
cookies_dict = json.loads(cj)

PART2_LYRICS = """[01:28 Interlude 钢琴与大提琴交织，空间感展开]

[01:40 Verse 2 主歌二]
星尘掠过舷窗 结成了冰霜
指引着迷航者 最后的微光
我们穿过尘埃 漫长的流浪
在静默的银河 听心跳回响

[02:10 Bridge 桥段 (情绪层层积蓄，交响渐强)]
黑洞无法吞噬 彼此的凝望
磁场交错编织 坚定的信标
撕裂所有虚空 奔赴那极光
哪怕燃尽一切 也照亮苍穹！

[02:35 Chorus 2 终极副歌 (全乐器爆发，空灵高亢)]
就在这 绚烂的星穹之巅！
折射出 你我重逢的象限！
不需要 氧气和引力成全！
频率共振 就是永远！
穿越了 亿万光年的荒原！
你是我 唯一不灭的终点！

[03:00 Outro 尾奏与余韵]
(空灵轻吟) 极光... 变奏...
越过星云隧门...
飞向属于我们的... 宇宙...
[Fade Out 钢琴泛音渐隐]
[End]"""

driver, _ = build_cloak_driver(proxy='http://127.0.0.1:7890', headless=True)
try:
    print("[*] Loading Suno /create...")
    driver.get('https://suno.com/')
    time.sleep(2)
    driver.page.context.add_cookies([{'name': k, 'value': v, 'url': 'https://suno.com'} for k, v in cookies_dict.items()])
    driver.get('https://suno.com/create')
    time.sleep(6)
    
    # 1. More options
    print("[*] Opening song options menu...")
    driver.page.evaluate('''() => {
        const btns = Array.from(document.querySelectorAll('button[aria-label="More options"]'));
        if (btns.length > 0) btns[0].click();
    }''')
    time.sleep(1.5)
    
    # 2. Edit
    print("[*] Selecting Edit...")
    driver.page.evaluate('''() => {
        const editItem = Array.from(document.querySelectorAll('[role="menuitem"]')).find(el => (el.innerText || '').trim() === 'Edit');
        if (editItem) {
            editItem.dispatchEvent(new PointerEvent('pointerenter', { bubbles: true }));
            editItem.click();
        }
    }''')
    time.sleep(1.5)
    
    # 3. Extend
    print("[*] Selecting Extend...")
    driver.page.evaluate('''() => {
        const ext = Array.from(document.querySelectorAll('[role="menuitem"]')).find(el => (el.innerText || '').trim() === 'Extend');
        if (ext) ext.click();
    }''')
    time.sleep(3)
    
    # 4. Click Select All
    print("[*] Clicking Select All to extend from end (01:28.0)...")
    driver.page.evaluate('''() => {
        const selectAllBtn = Array.from(document.querySelectorAll('button')).find(b => (b.innerText || '').trim() === 'Select All');
        if (selectAllBtn) selectAllBtn.click();
    }''')
    time.sleep(1.5)
    
    # 5. Fill Part 2 Lyrics
    print("[*] Filling Part 2 lyrics...")
    lyrics_set = driver.page.evaluate('''(lyricsText) => {
        const tas = Array.from(document.querySelectorAll('textarea'));
        // Find the lyrics textarea (first or one with lyrics placeholder)
        const lyricsTa = tas.find(t => t.placeholder.includes('lyrics') || t.placeholder === '') || tas[0];
        if (lyricsTa) {
            lyricsTa.focus();
            const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, "value").set;
            nativeSetter.call(lyricsTa, lyricsText);
            lyricsTa.dispatchEvent(new Event('input', { bubbles: true }));
            lyricsTa.dispatchEvent(new Event('change', { bubbles: true }));
            return { success: true, length: lyricsTa.value.length };
        }
        return { success: false };
    }''', PART2_LYRICS)
    print("[*] Lyrics injection result:", lyrics_set)
    time.sleep(2)
    driver.page.screenshot(path="before_create_extend.png")
    
    # 6. Click Create!
    print("[*] Clicking Create button...")
    create_result = driver.page.evaluate('''() => {
        const createBtn = Array.from(document.querySelectorAll('button')).find(b => {
            const txt = (b.innerText || '').trim();
            return txt.includes('Create') && !b.disabled;
        });
        if (createBtn) {
            createBtn.click();
            return { clicked: true, text: createBtn.innerText };
        }
        return { clicked: false };
    }''')
    print("[*] Create button click result:", create_result)
    
    # 7. Wait and poll generation
    print("[*] Polling for generation to start and complete...")
    for i in range(18):
        time.sleep(10)
        driver.page.screenshot(path=f"extend_progress_{i}.png")
        clips = driver.page.evaluate('''() => {
            const cards = Array.from(document.querySelectorAll('[class*="track"], [class*="song-row"], [class*="TrackItem"]'));
            // Or get songs by text
            const songRows = Array.from(document.querySelectorAll('div')).filter(d => d.innerText && d.innerText.includes('频率共振') && d.innerText.includes('V6-MINI'));
            return songRows.map(r => r.innerText.slice(0, 150));
        }''')
        print(f"[{i*10}s] Track list preview count:", len(clips))
        if len(clips) >= 3:
            print("[*] Detected new clips appearing in workspace!")
            
    driver.page.screenshot(path="final_extend_workspace.png")
    print("[*] Generation monitoring finished.")

finally:
    driver.quit()

# -*- coding: utf-8 -*-
import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
import requests, json, time

with open('data/rh_accounts/CatherineAndrews17889LE@outlook.com.json', 'r', encoding='utf-8') as f:
    acc = json.load(f)

token = acc['token']
proxy = acc['proxy']
proxies = {'http': proxy, 'https': proxy} if proxy else None
headers = {
    'Authorization': f'Bearer {token}',
    'Accept': 'application/json, text/plain, */*',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Origin': 'https://www.runninghub.ai',
    'Referer': 'https://www.runninghub.ai/ai-detail/2102677348907102210',
    'Content-Type': 'application/json'
}

wid = '2102677348907102210'
test_prompt = "Cyberpunk cat wearing neon goggles, futuristic Tokyo alley, 8k resolution, highly detailed"

payload = {
    "webappId": wid,
    "inputs": [
        {"nodeId": "13", "fieldName": "aspect_ratio", "fieldValue": "1:1 (Square)"},
        {"nodeId": "13", "fieldName": "megapixels", "fieldValue": "1"},
        {"nodeId": "477", "fieldName": "value", "fieldValue": test_prompt}
    ]
}

print("Submitting test prompt to RunningHub ComfyUI...")
r = requests.post("https://www.runninghub.ai/task/webapp/create", headers=headers, json=payload, proxies=proxies, timeout=20)
print("Create status:", r.status_code)
resp_data = r.json()
print("Create response:", json.dumps(resp_data, ensure_ascii=False, indent=2))

if resp_data.get('code') == 0:
    task_id = resp_data.get('data', {}).get('taskId') or resp_data.get('data')
    print(f"\n🎉 任务提交成功！Task ID: {task_id}")
    print("正在轮询任务执行状态...")

    for i in range(30):
        time.sleep(3)
        # Query task history
        q_body = {"size": 5, "current": 1, "taskType": ["WORKFLOW", "WEBAPP"]}
        r_hist = requests.post("https://www.runninghub.ai/api/output/v2/history", headers=headers, json=q_body, proxies=proxies, timeout=15)
        hist_data = r_hist.json().get('data', {}).get('records', [])
        
        found = False
        for item in hist_data:
            tid = str(item.get('taskId') or item.get('id') or '')
            if tid == str(task_id):
                status = item.get('taskStatus') or item.get('status')
                progress = item.get('progress') or '0'
                print(f"[{i*3}s] 状态: {status} | 进度: {progress}%")
                found = True
                if status in ['SUCCESS', 'FINISHED', 'COMPLETED']:
                    print("🎉 任务执行完成！产物列表:")
                    outputs = item.get('fileList') or item.get('outputs') or []
                    print(json.dumps(outputs, indent=2))
                    break
                elif status in ['FAILED', 'ERROR']:
                    print("❌ 任务执行失败:", item.get('errorMessage'))
                    break
        if not found and hist_data:
            print(f"[{i*3}s] 最近任务状态:", hist_data[0].get('taskStatus'), hist_data[0].get('taskId'))

import time
import requests
import json
import os

API_BASE = "http://localhost:5055"

def test_full_video_api_lifecycle():
    print(f"\n=======================================================")
    print(f"[*] Testing Grok Imagine Video API Endpoint Lifecycle")
    print(f"=======================================================\n")

    # 1. Health check
    print("1. Checking service health...")
    r = requests.get(f"{API_BASE}/")
    assert r.status_code == 200, f"Health check failed: {r.text}"
    print(f"[+] Health check OK: {r.json()['service']} ({r.json()['status']})")

    # 2. Check quota summary
    print("\n2. Checking quota summary...")
    r = requests.get(f"{API_BASE}/v1/videos/quota/summary")
    assert r.status_code == 200
    summary = r.json()
    print(f"[+] Total healthy accounts available: {summary['total_healthy_accounts']}")

    # 3. Create video generation task
    print("\n3. Creating video generation task...")
    payload = {
        "model": "grok-imagine-video",
        "prompt": "A cute fluffy kitten chasing a butterfly in garden, warm sunlight, slow motion 4k",
        "duration": 6,
        "resolution": "720p",
        "aspect_ratio": "2:3"
    }
    r = requests.post(f"{API_BASE}/v1/videos/generations", json=payload)
    assert r.status_code == 202, f"Create failed: {r.text}"
    job = r.json()
    task_id = job["id"]
    poll_url = job.get("poll_url") or f"{API_BASE}/v1/videos/{task_id}"
    print(f"[+] Task created successfully!")
    print(f"    Task ID:  {task_id}")
    print(f"    Status:   {job['status']}")
    print(f"    Poll URL: {poll_url}")

    # 4. Polling for progress & completion
    print("\n4. Polling video generation progress (waiting for GPU rendering)...")
    final_task = None
    start_time = time.time()
    for poll_step in range(60):
        time.sleep(3)
        elapsed = int(time.time() - start_time)
        res = requests.get(f"{API_BASE}/v1/videos/{task_id}").json()
        status = res.get("status")
        progress = res.get("progress", 0)
        account = res.get("account_used") or "allocating..."

        print(f"    [{elapsed}s] Status: {status} | Progress: {progress}% | Account: {account}")

        if status == "completed":
            final_task = res
            break
        elif status == "failed":
            raise RuntimeError(f"Video generation task failed: {res.get('error')}")

    if not final_task:
        raise TimeoutError("Video generation timed out after 180s")

    print(f"\n=======================================================")
    print(f"[SUCCESS] Video Generation Completed via API!")
    print(f"Task ID:      {final_task['id']}")
    print(f"Content URL:  {final_task.get('content_url')}")
    print(f"Remote URL:   {final_task.get('remote_url')}")
    print(f"File Size:    {final_task.get('file_size')} bytes ({final_task.get('file_size', 0)/1024/1024:.2f} MB)")
    print(f"Dimensions:   {final_task.get('width')} x {final_task.get('height')}")
    print(f"Account:      {final_task.get('account_used')}")
    print(f"=======================================================\n")

    # 5. Test direct download endpoint
    print("5. Testing direct video download from /v1/videos/{task_id}/content ...")
    content_resp = requests.get(f"{API_BASE}/v1/videos/{task_id}/content", stream=True)
    assert content_resp.status_code == 200, f"Download failed: {content_resp.status_code}"
    assert "video/mp4" in content_resp.headers.get("Content-Type", "")

    test_download_path = f"scratch/api_downloaded_{task_id}.mp4"
    with open(test_download_path, "wb") as f:
        for chunk in content_resp.iter_content(chunk_size=8192):
            f.write(chunk)

    downloaded_size = os.path.getsize(test_download_path)
    print(f"[+] Download verified! Saved to {test_download_path}")
    print(f"[+] Downloaded File Size: {downloaded_size} bytes ({downloaded_size/1024/1024:.2f} MB)")
    print(f"\nAPI Lifecycle Test Passed 100%!")

if __name__ == "__main__":
    test_full_video_api_lifecycle()

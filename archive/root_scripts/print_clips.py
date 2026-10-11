import json
import os

with open('f:/github/turb-gpt-free-register/final_generation_log.json', encoding='utf-8') as f:
    data = json.load(f)

print("=== REQUESTS WITH POST DATA ===")
for req in data.get("requests", []):
    if req.get("post_data") and "generate" in req.get("url", ""):
        print(f"URL: {req.get('url')}")
        print(f"Headers: {json.dumps(req.get('headers'), indent=2)}")
        print(f"Post Data: {req.get('post_data')}")
        print("-" * 40)

print("\n=== COMPLETED CLIPS ===")
clips = data.get("completed_clips", [])
print(f"Total Completed Clips: {len(clips)}")
for c in clips:
    print(f"ID: {c.get('id')}")
    print(f"Title: {c.get('title')}")
    print(f"Status: {c.get('status')}")
    print(f"Audio URL: {c.get('audio_url')}")
    print(f"Image URL: {c.get('image_url')}")
    print(f"Video URL: {c.get('video_url')}")
    print(f"Duration: {c.get('metadata', {}).get('duration')}")
    print("-" * 40)

# Check files on disk
print("\n=== LOCAL DOWNLOADED FILES ===")
if os.path.exists("generated_music"):
    for fn in os.listdir("generated_music"):
        fp = os.path.join("generated_music", fn)
        print(f"File: {fn} ({os.path.getsize(fp)} bytes)")

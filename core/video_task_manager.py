import time
import uuid
import threading
from typing import Dict, Any, Optional, List

class VideoTaskManager:
    """线程安全的视频生成任务管理器"""
    def __init__(self):
        self._tasks: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def create_task(self, prompt: str, model: str = "grok-imagine-video", duration: int = 6, resolution: str = "720p", aspect_ratio: str = "2:3") -> Dict[str, Any]:
        task_id = f"video_{uuid.uuid4().hex[:16]}"
        now = int(time.time())
        task_info = {
            "id": task_id,
            "object": "video.job",
            "model": model,
            "prompt": prompt,
            "duration": duration,
            "resolution": resolution,
            "aspect_ratio": aspect_ratio,
            "status": "queued",  # queued, in_progress, completed, failed
            "progress": 0,
            "created_at": now,
            "updated_at": now,
            "completed_at": None,
            "account_used": None,
            "video_url": None,
            "remote_url": None,
            "file_path": None,
            "file_size": 0,
            "width": None,
            "height": None,
            "error": None
        }
        with self._lock:
            self._tasks[task_id] = task_info
        return task_info

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            t = self._tasks.get(task_id)
            return dict(t) if t else None

    def update_task(self, task_id: str, **kwargs) -> Optional[Dict[str, Any]]:
        with self._lock:
            if task_id not in self._tasks:
                return None
            self._tasks[task_id].update(kwargs)
            self._tasks[task_id]["updated_at"] = int(time.time())
            return dict(self._tasks[task_id])

    def list_tasks(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._lock:
            all_tasks = sorted(self._tasks.values(), key=lambda x: x["created_at"], reverse=True)
            return [dict(t) for t in all_tasks[:limit]]

task_manager = VideoTaskManager()

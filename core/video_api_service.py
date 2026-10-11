import os
from pathlib import Path
from typing import Optional, List
from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from core.video_task_manager import task_manager
from core.video_worker import execute_video_generation, get_db_accounts, check_account_video_quota

app = FastAPI(
    title="Grok Imagine Video API Service",
    description="高并发标准异步视频生成 API 服务端点，支持文生视频、进度轮询、即时流式下载与额度统计",
    version="1.0.0"
)

# 允许跨域，方便前端与外部系统直接调用
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class CreateVideoRequest(BaseModel):
    prompt: str = Field(..., description="视频生成提示词", example="A cute golden retriever puppy running on grass, cinematic 4k")
    model: str = Field("grok-imagine-video", description="视频模型标识", example="grok-imagine-video")
    duration: int = Field(6, description="视频生成时长(秒)，支持 6, 10, 15", example=6)
    resolution: str = Field("720p", description="视频分辨率，支持 720p, 480p", example="720p")
    aspect_ratio: str = Field("2:3", description="视频比例，支持 2:3, 16:9, 1:1, 9:16", example="2:3")

@app.post("/v1/videos/generations", summary="创建视频生成任务", response_description="任务状态对象")
@app.post("/v1/videos", summary="创建视频生成任务 (短路由兼容)")
async def create_video_generation(req: CreateVideoRequest, background_tasks: BackgroundTasks, request: Request):
    """
    提交视频生成请求，立即返回 task_id 与 queued 状态，任务将在后台自动执行。
    """
    if not req.prompt.strip():
        raise HTTPException(status_code=400, detail="prompt cannot be empty")

    task = task_manager.create_task(
        prompt=req.prompt.strip(),
        model=req.model,
        duration=req.duration,
        resolution=req.resolution,
        aspect_ratio=req.aspect_ratio
    )
    task_id = task["id"]

    # 提交到后台异步执行
    background_tasks.add_task(execute_video_generation, task_id)

    # 组装返回数据，包含绝对可访问的轮询地址
    base_url = str(request.base_url).rstrip("/")
    task_response = dict(task)
    task_response["poll_url"] = f"{base_url}/v1/videos/{task_id}"

    return JSONResponse(status_code=202, content=task_response)

@app.get("/v1/videos/{task_id}", summary="轮询查询视频生成进度与结果")
async def get_video_status(task_id: str, request: Request):
    """
    前端或调用方定期轮询该接口获取视频生成进度 (0-100%)。
    当 status 为 completed 时，包含 video_url 供直接下载或播放。
    """
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Video task '{task_id}' not found")

    base_url = str(request.base_url).rstrip("/")
    res = dict(task)
    if res.get("video_url"):
        res["content_url"] = f"{base_url}{res['video_url']}"
    return res

@app.get("/v1/videos/{task_id}/content", summary="即时流式下载或播放生成的 .mp4 视频")
async def get_video_content(task_id: str):
    """
    直接返回已生成完成的 MP4 二进制流，支持浏览器在线播放与即时保存。
    """
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Video task '{task_id}' not found")

    if task.get("status") != "completed":
        raise HTTPException(status_code=409, detail=f"Video is not ready yet. Current status: {task.get('status')}, progress: {task.get('progress')}%")

    file_path = task.get("file_path")
    if not file_path or not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Video file not found on local disk")

    return FileResponse(
        path=file_path,
        media_type="video/mp4",
        filename=f"{task_id}.mp4"
    )

@app.get("/v1/videos/quota/summary", summary="查看账号池可用视频生成额度统计")
async def get_video_quota_summary():
    """
    查询当前账号池全部账号的视频额度统计信息。
    """
    accounts = get_db_accounts()
    total_accounts = len(accounts)

    return {
        "total_healthy_accounts": total_accounts,
        "model": "grok-imagine-video",
        "supported_resolutions": ["720p", "480p"],
        "supported_durations": [6, 10, 15],
        "quota_window": "24 hours per account",
        "description": "Each free Grok account receives 1 video (720p/480p) quota per 24-hour cycle."
    }

@app.get("/v1/videos", summary="列出最近生成的视频任务")
async def list_recent_videos(limit: int = 20):
    return task_manager.list_tasks(limit=limit)

@app.get("/", summary="服务健康检查")
async def health_check():
    return {
        "service": "Grok Imagine Video API",
        "status": "online",
        "endpoints": {
            "create": "POST /v1/videos/generations",
            "status": "GET /v1/videos/{task_id}",
            "content": "GET /v1/videos/{task_id}/content",
            "quota": "GET /v1/videos/quota/summary",
            "docs": "GET /docs"
        }
    }

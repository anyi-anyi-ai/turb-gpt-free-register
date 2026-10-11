# -*- coding: utf-8 -*-
"""
vision_solver — 通用视觉验证码求解器（库）

用 CaptchaSpec 描述一类验证码（frame/选择器/prompt/交互模式），用多模型视觉投票求解，
通用 driver 据答案驱动浏览器（多选点格子+Verify / 单选导航+Submit）。

深度扩展：
1. 本地 Ollama 端侧 VLM 双阶段抗幻觉思维链 (Two-Stage CoT Prompting)
2. 自适应打码模型多级智能路由器 (CaptchaModelRouter)
3. 专精 ONNX / 本地 Whisper / 端侧 Qwen2.5-VL / 云端投票全链路自适应调度
"""

from .schema import CaptchaSpec, build_prompt, GRID_PROMPT, SINGLE_PROMPT, DRAG_PROMPT
from .solver import solve, VisionSolver
from .vision import ask_vision, vote_answer, vote_picklist, vote_points
from .imaging import (
    shot_element, screenshot_frame_region, stitch_options_grid,
    enhance_local, annotate_choice, parse_json_answer,
    overlay_grid_numbers, annotate_canvas_choice,
)
from .vlm_cot_solver import OllamaVLMSolver, solve_with_vlm_cot, CoTDecision
from .model_router import CaptchaModelRouter, route_captcha, RouteDecision

__all__ = [
    "CaptchaSpec", "build_prompt", "GRID_PROMPT", "SINGLE_PROMPT", "DRAG_PROMPT",
    "solve", "VisionSolver",
    "ask_vision", "vote_answer", "vote_picklist", "vote_points",
    "shot_element", "screenshot_frame_region", "stitch_options_grid",
    "enhance_local", "annotate_choice", "parse_json_answer",
    "overlay_grid_numbers", "annotate_canvas_choice",
    "OllamaVLMSolver", "solve_with_vlm_cot", "CoTDecision",
    "CaptchaModelRouter", "route_captcha", "RouteDecision",
]

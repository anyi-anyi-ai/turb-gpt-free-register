# -*- coding: utf-8 -*-
"""
tests/test_webui_scripts_schema.py — 验证 WebUI 任务脚本 Schema 的完整性与命令构建
"""
import os
import sys
from pathlib import Path
import pytest

from webui import scripts as schema
from webui.server import _build_cmd, ROOT


def test_unique_script_ids():
    ids = [s["id"] for s in schema.SCRIPTS]
    duplicates = [sid for sid in ids if ids.count(sid) > 1]
    assert len(ids) == len(set(ids)), f"存在重复的脚本 ID: {set(duplicates)}"


def test_registered_script_files_exist():
    for s in schema.SCRIPTS:
        file_path = os.path.join(ROOT, s["file"])
        assert os.path.isfile(file_path), f"脚本文件不存在: {s['id']} -> {file_path}"


def test_multimodal_scripts_presence():
    multimodal_ids = [
        "run_suno_register",
        "run_elevenlabs_register",
        "run_udio_register",
        "run_stableaudio_register",
        "run_muse_register",
        "run_runninghub_register",
        "register_grok_http",
        "run_proxy_harvester",
    ]
    for sid in multimodal_ids:
        script = schema.script_by_id(sid)
        assert script is not None, f"未找到脚本 Schema: {sid}"
        assert script["category"] in {"多模态与媒体", "单平台注册", "协议与基建"}
        assert len(script["args"]) > 0


def test_build_cmd_suno():
    script = schema.script_by_id("run_suno_register")
    args = {
        "--count": 3,
        "--email": "user@outlook.com",
        "--proxy": "http://127.0.0.1:7890",
        "--no-headless": True,
        "--no-sync": True,
    }
    cmd = _build_cmd(script, args)
    cmd_str = " ".join(cmd)
    assert "run_suno_register.py" in cmd_str
    assert "--count 3" in cmd_str
    assert "--email user@outlook.com" in cmd_str
    assert "--proxy http://127.0.0.1:7890" in cmd_str
    assert "--no-headless" in cmd_str
    assert "--no-sync" in cmd_str


def test_build_cmd_elevenlabs():
    script = schema.script_by_id("run_elevenlabs_register")
    args = {
        "--count": 2,
        "--continuous": True,
        "--headless": True,
    }
    cmd = _build_cmd(script, args)
    cmd_str = " ".join(cmd)
    assert "run_elevenlabs_register.py" in cmd_str
    assert "--count 2" in cmd_str
    assert "--continuous" in cmd_str
    assert "--headless" in cmd_str


def test_build_cmd_grok_http():
    script = schema.script_by_id("register_grok_http")
    args = {
        "--count": 5,
        "--sub2api": True,
        "--sub2api-group": "default_grok",
    }
    cmd = _build_cmd(script, args)
    cmd_str = " ".join(cmd)
    assert "register_grok_http.py" in cmd_str
    assert "--count 5" in cmd_str
    assert "--sub2api" in cmd_str
    assert "--sub2api-group default_grok" in cmd_str

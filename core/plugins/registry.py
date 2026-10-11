# -*- coding: utf-8 -*-
"""
core/plugins/registry.py — 网站注册插件注册中心与自省发现引擎 (PluginRegistry)
负责：
  - 插件类的注册装饰器 (@register_plugin)
  - 自动扫描 plugins/ 目录下的所有独立插件文件
  - 向外输出机器可读 (Machine-Readable JSON) 的平台能力清册与状态
"""
from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Type

from core.plugins.base import BaseSitePlugin

logger = logging.getLogger("turb.plugin.registry")

_ROOT = Path(__file__).resolve().parent.parent.parent


class PluginRegistry:
    """全局单例插件注册中心。"""

    _instance: Optional["PluginRegistry"] = None
    _registry: Dict[str, Type[BaseSitePlugin]] = {}
    _discovered: bool = False

    def __new__(cls) -> "PluginRegistry":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._registry = {}
            cls._discovered = False
        return cls._instance

    @classmethod
    def register(cls, plugin_cls: Type[BaseSitePlugin]) -> Type[BaseSitePlugin]:
        """将插件类注册到全局中枢。"""
        if not plugin_cls.id or plugin_cls.id == "unknown":
            raise ValueError(f"插件类 {plugin_cls.__name__} 必须定义唯一的 id 属性")
        cls._registry[plugin_cls.id.lower().strip()] = plugin_cls
        logger.debug(f"[PluginRegistry] 注册插件成功: {plugin_cls.id} ({plugin_cls.name})")
        return plugin_cls

    @classmethod
    def get(cls, plugin_id: str) -> Optional[Type[BaseSitePlugin]]:
        """获取指定 id 的插件类。"""
        cls.ensure_discovered()
        return cls._registry.get(plugin_id.lower().strip())

    @classmethod
    def list_all(cls) -> List[Dict[str, Any]]:
        """列出当前所有已加载插件的元数据。"""
        cls.ensure_discovered()
        results = []
        for pid, p_cls in cls._registry.items():
            results.append({
                "id": pid,
                "name": p_cls.name,
                "description": p_cls.description,
                "target_url": p_cls.target_url,
                "table_name": p_cls.table_name,
                "browser_type": p_cls.browser_type,
                "requires_human": p_cls.requires_human,
                "default_headless": p_cls.default_headless,
            })
        return sorted(results, key=lambda x: x["id"])

    @classmethod
    def export_manifest(cls) -> Dict[str, Any]:
        """导出机器可读的声明式架构清单 (专供 Agent 秒级自省)。"""
        plugins = cls.list_all()
        return {
            "version": "2.6.0",
            "total_plugins": len(plugins),
            "usage": "python turb.py run <platform_id> [--count N] [--email target@outlook.com] [--headful]",
            "plugins": plugins,
        }

    @classmethod
    def ensure_discovered(cls) -> None:
        """动态扫描并加载 plugins/ 目录下的所有插件。"""
        if cls._discovered:
            return

        plugins_dir = _ROOT / "plugins"
        if plugins_dir.is_dir():
            for py_file in plugins_dir.glob("*.py"):
                if py_file.name.startswith("_"):
                    continue
                module_name = f"plugins.{py_file.stem}"
                try:
                    spec = importlib.util.spec_from_file_location(module_name, str(py_file))
                    if spec and spec.loader:
                        mod = importlib.util.module_from_spec(spec)
                        sys.modules[module_name] = mod
                        spec.loader.exec_module(mod)
                except Exception as e:
                    logger.error(f"[PluginRegistry] 加载插件 {py_file.name} 失败: {e}", exc_info=True)

        cls._discovered = True


def register_plugin(cls: Type[BaseSitePlugin]) -> Type[BaseSitePlugin]:
    """快捷注册装饰器。"""
    return PluginRegistry.register(cls)

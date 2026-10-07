# -*- coding: utf-8 -*-
"""[已弃用] Kiro 注册入口兼容包装。请统一使用 register_kiro.py。"""
import runpy
import sys
import warnings

warnings.warn(
    "run_kiro_register.py 已弃用，请统一调用 register_kiro.py",
    DeprecationWarning,
    stacklevel=2,
)

if __name__ == "__main__":
    runpy.run_module("register_kiro", run_name="__main__")

# -*- coding: utf-8 -*-
"""[已弃用] Outlook 注册入口兼容包装。请统一使用 register_outlook_standalone.py。"""
import runpy
import sys
import warnings

warnings.warn(
    "run_outlook_register.py 已弃用，请统一调用 register_outlook_standalone.py",
    DeprecationWarning,
    stacklevel=2,
)

if __name__ == "__main__":
    runpy.run_module("register_outlook_standalone", run_name="__main__")

# -*- coding: utf-8 -*-
"""ChatGPT 网页端认证交互流水线。"""
from . import human_actions, stages, email_step, password_step, otp_step, profile_step, session_step

_SUBMODULES = (human_actions, stages, email_step, password_step, otp_step, profile_step, session_step)

for mod in _SUBMODULES:
    for k, v in vars(mod).items():
        if not k.startswith("__"):
            globals()[k] = v

__all__ = [k for k in list(globals().keys()) if not k.startswith("__")]

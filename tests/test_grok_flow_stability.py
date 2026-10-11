# -*- coding: utf-8 -*-
"""CloakBrowser 复杂网络表单稳定性与 Turnstile 守卫测试。"""
import pytest
from core.grok.flow import GrokSignupFlow


def test_wait_turnstile_solved_guard():
    # 测试提交前 Turnstile Token 守卫
    class DummyPage:
        def __init__(self, token_len=0):
            self.token_len = token_len

        def evaluate(self, script, *args):
            return {"has_cf": True, "token_len": self.token_len}

    # 1. 未达到 80 长度，超时返回 False
    page_pending = DummyPage(token_len=25)
    flow = GrokSignupFlow(page=page_pending)
    assert flow.wait_turnstile_solved(timeout_sec=0.1) is False

    # 2. 达到 80 长度，立即返回 True
    page_solved = DummyPage(token_len=85)
    flow_solved = GrokSignupFlow(page=page_solved)
    assert flow_solved.wait_turnstile_solved(timeout_sec=1) is True

    # 3. 页面无 CF 验证码，立即放行通过
    class NoCfPage:
        def evaluate(self, script, *args):
            return {"has_cf": False, "token_len": 0}

    flow_no_cf = GrokSignupFlow(page=NoCfPage())
    assert flow_no_cf.wait_turnstile_solved(timeout_sec=1) is True

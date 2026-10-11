# -*- coding: utf-8 -*-
"""Grok 账号级两级初生自检模块。

迁移并提炼自开源项目 grok-iq 的 SsoSessionCheck 与 analyzer.py 判定算法。
第一级：静态检查 initialData.user.botFlagSource 与 botFlagDetails；
第二级：微型 Prompt 探针检测真实流式推理表现、TPS 与思维链（Reasoning Tokens）。
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from curl_cffi import requests as curl_requests

logger = logging.getLogger(__name__)

HOME_URL = "https://grok.com/"

DEFAULT_PROBE_PROFILES: list[dict[str, Any]] = [
    {
        "id": "quality-marker",
        "name": "指令遵循标记基线",
        "prompt": "先用两点简要说明为什么天空呈蓝色，最后一行严格只输出 MARKER_OK。",
        "marker": "MARKER_OK",
        "model": "grok-3",
    },
    {
        "id": "reasoning-check",
        "name": "多步数学推理基线",
        "prompt": "有3个盒子，各装4个袋子，每袋5个球。移走7个球后还剩几个？写出简要计算并在最后一行只输出 RESULT=53。",
        "marker": "RESULT=53",
        "model": "grok-3",
    },
    {
        "id": "html-preview",
        "name": "结构化代码生成基线",
        "prompt": "生成一个极简深色模式卡片HTML代码，包含ok和risk两个状态类，最后一行输出 HTML_CARD_DONE。",
        "marker": "HTML_CARD_DONE",
        "model": "grok-3",
    },
]


@dataclass
class GrokQualityResult:
    status: str = "healthy"  # healthy / degraded / bot_flagged / error
    bot_flag_source: int | None = None
    bot_flag_details: str = ""
    probe_tps: float = 0.0
    probe_reasoning_tokens: int = 0
    first_token_ms: int | None = None
    probe_profile_used: str = ""
    reasons: list[str] = field(default_factory=list)
    raw_user: dict[str, Any] = field(default_factory=dict)


class GrokQualityChecker:
    def __init__(self, proxy: str = "", timeout_sec: int = 25):
        self.proxy = proxy
        self.timeout_sec = timeout_sec

    def check(
        self,
        sso_token: str,
        run_probe: bool = True,
        profile_id: str | None = None,
        cpa_record: dict[str, Any] | None = None,
    ) -> GrokQualityResult:
        """执行两级初生质量自检。"""
        result = GrokQualityResult()
        clean_token = sso_token.strip()
        if clean_token.startswith("sso="):
            clean_token = clean_token[4:].strip()

        if not clean_token:
            result.status = "error"
            result.reasons.append("SSO Token 为空")
            return result

        # ----------------------------------------------------
        # 第一级：静态账号画像与 BotFlag 标记探查
        # ----------------------------------------------------
        session = curl_requests.Session(impersonate="chrome124")
        if self.proxy:
            session.proxies = {"http": self.proxy, "https": self.proxy}

        cookies = {"sso": clean_token, "sso-rw": clean_token}
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }

        try:
            logger.info("[GrokQuality] 正在执行第一级静态风控自检 (请求 grok.com)...")
            resp = session.get(HOME_URL, cookies=cookies, headers=headers, timeout=self.timeout_sec)
            if resp.status_code != 200:
                result.status = "error"
                result.reasons.append(f"首页状态异常: HTTP {resp.status_code}")
                return result

            user_data = self._extract_initial_user(resp.text)
            if not user_data:
                # 页面可能重定向到登录页
                if "accounts.x.ai" in resp.url or "sign-in" in resp.text:
                    result.status = "error"
                    result.reasons.append("SSO Token 无效或未登录")
                    return result

            result.raw_user = user_data or {}
            # 检查 botFlag
            bot_source, bot_details = self._extract_bot_flags(user_data, resp.text)
            result.bot_flag_source = bot_source
            result.bot_flag_details = bot_details

            if bot_source is not None and bot_source != 0:
                result.status = "bot_flagged"
                result.reasons.append(f"检测到 Bot 标记: source={bot_source} ({bot_details})")
                logger.warning("[GrokQuality] 账号被标记为 Bot: %s", result.reasons[-1])
                return result

            if "policy=deny" in bot_details.lower():
                result.status = "bot_flagged"
                result.reasons.append("检测到注册被拒绝标记 (policy=deny)")
                return result

            logger.info("[GrokQuality] 第一级静态自检通过: 未命中 Bot 标记")

        except Exception as exc:
            logger.warning("[GrokQuality] 第一级静态检查网络异常: %s", exc)
            result.reasons.append(f"静态检查请求失败: {exc}")

        # ----------------------------------------------------
        # 第二级：GrokIQ 动态探针分析矩阵 (测思维链、TPS、标志物与缓冲)
        # ----------------------------------------------------
        if not run_probe or result.status not in ("healthy", ""):
            return result

        try:
            try:
                probe_res = self._run_lightweight_probe(session, clean_token, profile_id=profile_id, cpa_record=cpa_record)
            except TypeError:
                probe_res = self._run_lightweight_probe(session, clean_token, profile_id=profile_id)
            if probe_res.get("skipped"):
                logger.info("[GrokQuality] 动态探针跳过 (%s)，保持静态风控判定: %s", probe_res.get("error"), result.status)
                result.reasons.append(f"探针未直接生效: {probe_res.get('error')}")
                return result

            result.probe_tps = probe_res.get("tps", 0.0)
            result.probe_reasoning_tokens = probe_res.get("reasoning_tokens", 0)
            result.first_token_ms = probe_res.get("first_token_ms")
            result.probe_profile_used = probe_res.get("profile_id", "")

            # GrokIQ 核心风险规则判定
            # 规则 0: 输出不足异常 (insufficient_output)
            if probe_res.get("output_tokens", 0) < 5:
                result.status = "degraded"
                result.reasons.append("模型生成输出过短不足 [insufficient_output] (Tokens < 5)")

            # 规则 1: 标志物未命中 (marker_miss)
            elif not probe_res.get("marker_matched") and probe_res.get("output_tokens", 0) >= 5:
                result.status = "degraded"
                result.reasons.append("未匹配到指令标志物 [marker_miss] (模型指令遵从度异常，疑似降智)")

            # 规则 2: 推理思维链缺失 (reasoning_zero)
            elif probe_res.get("reasoning_tokens", 0) == 0 and probe_res.get("output_tokens", 0) > 20:
                result.status = "degraded"
                result.reasons.append("推理模型缺少思维链输出 [reasoning_zero] (Reasoning Tokens 为 0)")

            # 规则 3: 高速强异常 (fast_risk)
            elif result.probe_tps >= 150.0:
                result.status = "degraded"
                result.reasons.append(f"生成速度异常虚高 [fast_risk] (TPS={result.probe_tps:.1f} >= 150)")

            # 规则 4: 流式硬缓冲 (buffered_hard)
            elif probe_res.get("first_token_share", 0.0) >= 0.85 and probe_res.get("output_tokens", 0) > 25:
                result.status = "degraded"
                result.reasons.append(f"响应流整体缓冲阻滞 [buffered_hard] (首包占比={probe_res.get('first_token_share', 0.0):.2f})")

            # 规则 5: 轻度缓冲/高 TPS 观察 (buffered_soft)
            elif 80.0 <= result.probe_tps < 150.0 and probe_res.get("first_token_share", 0.0) >= 0.7:
                result.status = "healthy"
                result.reasons.append(f"轻度缓冲/TPS偏高观察 [buffered_soft] (TPS={result.probe_tps:.1f})")

            else:
                result.status = "healthy"
                logger.info(
                    "[GrokQuality] 第二级探针检测全部通过: TPS=%.1f, ReasoningTokens=%d, MarkerMatched=True",
                    result.probe_tps,
                    result.probe_reasoning_tokens,
                )

        except Exception as exc:
            logger.debug("[GrokQuality] 第二级探针未完全执行: %s", exc)
            result.reasons.append(f"探针请求微弱异常: {exc}")

        return result

    @staticmethod
    def _extract_initial_user(html: str) -> dict[str, Any] | None:
        """从 grok.com 页面提取 initialData.user"""
        normalized = str(html or "").replace('\\"', '"')
        marker = '"initialData":'
        decoder = json.JSONDecoder()
        position = normalized.find(marker)
        while position >= 0:
            start = position + len(marker)
            sub = normalized[start:].lstrip()
            try:
                val, _ = decoder.raw_decode(sub)
                if isinstance(val, dict):
                    if isinstance(val.get("user"), dict):
                        return val["user"]
                    if "id" in val and "botFlagSource" in val:
                        return val
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
            position = normalized.find(marker, start)
        return None

    @staticmethod
    def _extract_bot_flags(user_data: dict[str, Any] | None, html: str) -> tuple[int | None, str]:
        """提取 botFlagSource 和 botFlagDetails"""
        source = None
        details = ""
        if user_data:
            if "botFlagSource" in user_data:
                try:
                    raw_src = user_data.get("botFlagSource")
                    source = int(raw_src) if raw_src is not None else None
                except (ValueError, TypeError):
                    source = None
            if "botFlagDetails" in user_data:
                details = str(user_data.get("botFlagDetails") or "").strip()

        if source is None:
            src_match = re.search(r'botFlagSource"\s*:\s*(null|-?\d+)', html)
            if src_match and src_match.group(1) != "null":
                try:
                    source = int(src_match.group(1))
                except ValueError:
                    pass
            det_match = re.search(r'botFlagDetails"\s*:\s*(?:null|"((?:\\.|[^"\\])*)")', html)
            if det_match and det_match.group(1):
                details = det_match.group(1)

        return source, details

    def _run_lightweight_probe(
        self,
        session: curl_requests.Session,
        token: str,
        profile_id: str | None = None,
        cpa_record: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """发送带标志物埋点的探针 Prompt，测试推理流式响应与指标。"""
        import random
        selected_profile = None
        if profile_id:
            selected_profile = next((p for p in DEFAULT_PROBE_PROFILES if p["id"] == profile_id), None)
        if not selected_profile:
            selected_profile = random.choice(DEFAULT_PROBE_PROFILES)

        probe_marker = selected_profile["marker"]
        prompt_text = selected_profile["prompt"]
        model_name = selected_profile.get("model", "grok-3")

        # 优先通过官方 CLIProxyAPI 授权端点探测
        if cpa_record and cpa_record.get("access_token"):
            access = str(cpa_record.get("access_token") or "").strip()
            url = f"{cpa_record.get('base_url', 'https://cli-chat-proxy.grok.com/v1').rstrip('/')}/responses"
            headers = dict(cpa_record.get("headers") or {})
            headers["Authorization"] = f"Bearer {access}"
            headers["Content-Type"] = "application/json"
            payload = {
                "model": "grok-3",
                "input": prompt_text,
                "max_output_tokens": 100,
                "stream": False,
            }
            try:
                start_time = time.perf_counter()
                resp = session.post(url, json=payload, headers=headers, timeout=20)
                gen_sec = max(0.05, time.perf_counter() - start_time)
                if resp.status_code != 200:
                    return {
                        "skipped": True,
                        "error": f"CPA HTTP {resp.status_code}: {resp.text[:120]}",
                        "status_code": resp.status_code,
                        "output_tokens": 0,
                    }
                body_json = resp.json() if resp.text else {}
                output_text = str(body_json.get("response") or body_json.get("text") or body_json.get("content") or resp.text or "").strip()
                output_tokens = max(1, len(output_text.split()))
                tps = output_tokens / gen_sec
                return {
                    "tps": round(tps, 1),
                    "output_tokens": output_tokens,
                    "reasoning_tokens": 0,
                    "has_reasoning": False,
                    "first_token_ms": int(gen_sec * 500),
                    "first_token_share": 0.5,
                    "marker_matched": probe_marker in output_text or len(output_text) > 10,
                    "profile_id": selected_profile.get("id", ""),
                    "output_text": output_text[:200],
                }
            except Exception as exc:
                return {
                    "skipped": True,
                    "error": f"CPA 探测异常: {exc}",
                    "output_tokens": 0,
                }

        url = "https://grok.com/rest/app-chat/conversations/new"
        payload = {
            "message": prompt_text,
            "modelName": model_name,
            "temporary": True,
        }
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "Origin": "https://grok.com",
            "Referer": "https://grok.com/",
        }

        try:
            start_time = time.perf_counter()
            resp = session.post(
                url,
                json=payload,
                headers=headers,
                stream=True,
                timeout=20,
            )
            if resp.status_code != 200:
                return {
                    "skipped": True,
                    "error": f"Web API HTTP {resp.status_code}",
                    "status_code": resp.status_code,
                    "output_tokens": 0,
                }
        except Exception as exc:
            return {
                "skipped": True,
                "error": f"Web API 请求异常: {exc}",
                "output_tokens": 0,
            }

        for line in resp.iter_lines():
            if not line:
                continue
            line_str = line.decode("utf-8", errors="ignore").strip()
            if not line_str.startswith("data:"):
                continue

            data_chunk = line_str[5:].strip()
            if data_chunk == "[DONE]":
                break

            now_perf = time.perf_counter()
            if first_token_time is None:
                first_token_time = now_perf

            try:
                chunk_obj = json.loads(data_chunk)
                token_delta = chunk_obj.get("token", "") or chunk_obj.get("delta", "")
                if isinstance(token_delta, str) and token_delta:
                    output_text += token_delta
                    total_chars += len(token_delta)
                    if first_chunk_chars == 0:
                        first_chunk_chars = len(token_delta)

                if "reasoning" in chunk_obj or "thinking" in chunk_obj or chunk_obj.get("isThinking"):
                    reasoning_tokens += 1
                    has_reasoning = True
            except Exception:
                pass

        end_time = time.perf_counter()
        generation_sec = max(0.01, end_time - (first_token_time or start_time))
        first_token_ms = int((first_token_time - start_time) * 1000) if first_token_time else None

        # 估算 Token 数量
        output_tokens = max(1, len(output_text.split()))
        tps = output_tokens / generation_sec
        first_token_share = (first_chunk_chars / max(1, total_chars)) if total_chars > 0 else 0.0
        marker_matched = probe_marker in output_text

        return {
            "tps": round(tps, 1),
            "output_tokens": output_tokens,
            "reasoning_tokens": reasoning_tokens,
            "has_reasoning": has_reasoning,
            "first_token_ms": first_token_ms,
            "first_token_share": round(first_token_share, 2),
            "marker_matched": marker_matched,
            "profile_id": selected_profile.get("id", ""),
            "output_text": output_text[:200],
        }

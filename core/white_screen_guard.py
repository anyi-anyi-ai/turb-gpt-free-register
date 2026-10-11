# -*- coding: utf-8 -*-
"""
页面白屏检测与自动刷新自愈守卫 (White Screen Guard)

核心功能：
1. 精准识别真实白屏（排查 Cloudflare 质询盾，排查正常文本/输入框/按钮）；
2. 页面持续空白 3~5 秒自动触发浏览器原生刷新（左上角刷新）；
3. 刷新后在 2~8 秒动态等待并监测恢复；
4. 若连续刷新仍无反应，抛出 WhiteScreenError，联动动态住宅 IP 自动淘汰换新。
"""
from __future__ import annotations

import logging
import time
from typing import Callable, Any

logger = logging.getLogger("white_screen_guard")


class WhiteScreenError(RuntimeError):
    """页面持续白屏且自动刷新后仍无响应异常。"""
    pass


def is_white_screen(driver) -> bool:
    """快速检测当前页面是否处于彻底白屏（无实质内容）状态。
    
    精确排除：
    - 正常的 Cloudflare 质询页（Turnstile、5秒盾等）；
    - 包含可见输入框、按钮、表单、有效文字、图片的页面。
    """
    js_check = r"""
    () => {
        try {
            const url = window.location.href || '';
            // 1. 排除 Cloudflare 质询页
            if (url.includes('__cf_chl_') || url.includes('cf_chl_')) {
                return false;
            }
            if (document.querySelector('iframe[src*="cloudflare"], #challenge-running, #cf-please-wait, .cf-turnstile, #turnstile-wrapper, div[class*="challenge-"]')) {
                return false;
            }

            // 2. 检查 body 是否存在
            if (!document.body) {
                return true;
            }

            // 3. 检查是否有实质文字
            const innerText = (document.body.innerText || '').trim();
            if (innerText.length > 8) {
                return false;
            }

            // 4. 检查是否有可见的交互元素 (输入框, 按钮, 表单)
            const interactive = document.querySelectorAll('input, button, form, a, [role="button"], textarea, select');
            for (let el of interactive) {
                if (el.offsetWidth > 0 || el.offsetHeight > 0 || el.getClientRects().length > 0) {
                    return false;
                }
            }

            // 5. 检查是否有可见的标题或媒体
            const mediaOrHeadings = document.querySelectorAll('img, svg, canvas, h1, h2, h3, h4, p');
            for (let el of mediaOrHeadings) {
                if (el.offsetWidth > 0 || el.offsetHeight > 0 || el.getClientRects().length > 0) {
                    return false;
                }
            }

            // 无任何可见元素且无文字 -> 确认为白屏
            return true;
        } catch (e) {
            return false;
        }
    }
    """
    try:
        if hasattr(driver, "execute_script"):
            return bool(driver.execute_script(js_check))
        elif hasattr(driver, "evaluate"):
            return bool(driver.evaluate(js_check))
        elif hasattr(driver, "page") and hasattr(driver.page, "evaluate"):
            return bool(driver.page.evaluate(js_check))
    except Exception as e:
        logger.debug("[白屏检测] 执行 JS 失败，保守返回 False: %s", e)
    return False


def wait_with_white_screen_guard(
    driver,
    check_success_fn: Callable[[Any], Any],
    timeout: float = 30.0,
    blank_threshold: float = 4.0,
    max_refreshes: int = 2,
    poll_interval: float = 0.5,
    on_refresh: Callable[[], None] | None = None,
) -> Any:
    """带白屏检测与自动刷新自愈的元素/条件等待器。

    Args:
        driver: Selenium 或 Playwright 浏览器驱动
        check_success_fn: 目标检查回调，返回 truthy 代表页面正常并成功找到目标
        timeout: 总超时时间，秒
        blank_threshold: 持续白屏达到该秒数时，触发刷新（默认 4 秒）
        max_refreshes: 最多允许刷新次数（默认 2 次）
        poll_interval: 轮询检测间隔，秒
        on_refresh: 每次执行刷新时的附加回调
    """
    start_time = time.time()
    blank_start_time: float | None = None
    refreshed_count = 0

    while True:
        elapsed = time.time() - start_time
        if elapsed > timeout:
            raise TimeoutError(f"等待目标元素超时（共耗时 {elapsed:.1f} 秒）")

        # 1. 首先检查目标是否已成功加载
        try:
            result = check_success_fn(driver)
            if result:
                return result
        except Exception:
            pass

        # 2. 检测当前是否处于白屏
        blank = is_white_screen(driver)
        if blank:
            now = time.time()
            if blank_start_time is None:
                blank_start_time = now
            elif (now - blank_start_time) >= blank_threshold:
                if refreshed_count < max_refreshes:
                    refreshed_count += 1
                    logger.warning(
                        "[白屏守卫] ⚠️ 检测到页面持续空白超过 %.1f 秒，立即点击刷新 (第 %d/%d 次)...",
                        blank_threshold, refreshed_count, max_refreshes,
                    )
                    try:
                        if hasattr(driver, "refresh"):
                            try:
                                driver.refresh(timeout_ms=15000)
                            except TypeError:
                                driver.refresh()
                        elif hasattr(driver, "page") and hasattr(driver.page, "reload"):
                            driver.page.reload(wait_until="domcontentloaded", timeout=15000)
                        if on_refresh:
                            on_refresh()
                    except Exception as ref_err:
                        logger.warning("[白屏守卫] 执行刷新异常: %s", ref_err)

                    # 刷新后给予 2.0 秒初始加载缓冲，并重置白屏计时器
                    time.sleep(2.0)
                    blank_start_time = None
                    continue
                else:
                    logger.error(
                        "[白屏守卫] ❌ 页面持续白屏，且已执行 %d 次刷新依然无任何内容，判定彻底卡死！",
                        max_refreshes,
                    )
                    raise WhiteScreenError(f"页面持续白屏，经 {max_refreshes} 次刷新自愈后仍无任何内容")
        else:
            # 页面存在内容（或加载中），重置白屏起始时间
            blank_start_time = None

        time.sleep(poll_interval)

# -*- coding: utf-8 -*-
"""Cloudflare Turnstile / 5 秒盾检测、跨域穿透自动点击与人工接管等待模块。

适用对象：CloakBrowser (Playwright)、RoxyBrowser (Selenium)。
"""
from __future__ import annotations

import ctypes
import logging
import math
import random
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)


class VerificationTimeoutError(RuntimeError):
    """人机验证等待超时或人工未在规定时间内解决。"""


def is_cloudflare_challenge(driver: Any) -> bool:
    """快速判断当前页面是否处于 Cloudflare 人机验证/质询状态。"""
    # 1. DOM 核心特征绝对优先判断：如果页面已有交互输入框或主要按钮，绝不是盾
    try:
        dom_status = driver.execute_script(r"""
        try {
            // 如果页面存在可见且未禁用的输入框（email, text, password, code, number），绝不是盾
            const inputs = Array.from(document.querySelectorAll('input:not([type="hidden"])'));
            const hasVisibleInput = inputs.some(i => (i.offsetWidth > 0 || i.offsetHeight > 0) && !i.disabled);
            if (hasVisibleInput) {
                return { isChallenge: false, reason: 'has_visible_input' };
            }

            // 如果页面有登录/注册/提交相关可见按钮，绝不是盾
            const btns = Array.from(document.querySelectorAll('button, a'));
            const hasAuthButtons = btns.some(b => {
                const t = (b.innerText || '').toLowerCase();
                return (
                    t.includes('log in') || t.includes('sign up') || t.includes('登录') || t.includes('注册') ||
                    t.includes('continue') || t.includes('继续') || t.includes('ログイン') || t.includes('開始する') ||
                    t.includes('始める') || t.includes('サインアップ') || t.includes('get started')
                ) && (b.offsetWidth > 0 || b.offsetHeight > 0);
            });
            if (hasAuthButtons) {
                return { isChallenge: false, reason: 'has_auth_buttons' };
            }

            // 真正的 CF 盾特征：必须有明确的 challenge 容器或交互式 Turnstile checkbox
            const stage = document.querySelector('#challenge-stage, #cf-challenge-running, #challenge-error-title, #cf-stage, .cf-turnstile-wrapper');
            if (stage && (stage.offsetWidth > 30 || stage.offsetHeight > 30)) {
                return { isChallenge: true, reason: 'has_challenge_stage' };
            }

            const cfIframe = document.querySelector('iframe[src*="challenges.cloudflare.com"], iframe[src*="challenge-platform"]');
            if (cfIframe && (cfIframe.offsetWidth > 80 && cfIframe.offsetHeight > 25)) {
                return { isChallenge: true, reason: 'has_cf_iframe' };
            }

            // 多语言 Cloudflare 页面 Title 特征（覆盖日/芬/韩/英/法/德/西/荷/波等）
            const title = (document.title || '').toLowerCase();
            const cfTitles = [
                'just a moment', 'nur einen moment', 'un instant', 'un momento',
                'attention required', 'please wait', 'moment...', '確認中', '確認しています',
                'しばらくお待ちください', 'pieni hetki', '잠시만', 'wacht even',
                'poczekaj', 'подождите', 'attendez', 'espera un momento',
                'verifying you are human', 'cloudflare', 'ddos-guard'
            ];
            if (cfTitles.some(t => title.includes(t))) {
                return { isChallenge: true, reason: 'title_challenge' };
            }
        } catch (e) {}
        return { isChallenge: false, reason: 'default_no_challenge' };
        """)
        if isinstance(dom_status, dict):
            if dom_status.get("reason") in ('has_visible_input', 'has_auth_buttons'):
                return False
            if dom_status.get("isChallenge"):
                return True
        elif isinstance(dom_status, bool) and dom_status:
            return True
    except Exception:
        pass

    # 2. Page Title 多语言特征（在没有可见输入框时作为判断依据）
    title = ""
    try:
        if hasattr(driver, "page") and hasattr(driver.page, "title"):
            title = str(driver.page.title() or "")
        elif hasattr(driver, "title"):
            title = str(driver.title or "")
    except Exception:
        pass

    lower_title = title.lower().strip()
    cf_title_signatures = (
        "just a moment", "nur einen moment", "un instant", "un momento",
        "attention required", "please wait", "moment...", "確認中", "確認しています",
        "しばらくお待ちください", "pieni hetki", "잠시만", "wacht even",
        "poczekaj", "подождите", "attendez", "espera un momento",
        "verifying you are human", "cloudflare", "ddos-guard",
    )
    if any(sig in lower_title for sig in cf_title_signatures):
        return True

    # 3. 显式 URL 特征（仅在确认页面没有交互输入框时判定）
    try:
        url = str(getattr(driver, "current_url", "") or "")
    except Exception:
        url = ""

    if any(sig in url for sig in ("__cf_chl_rt_tk=", "__cf_chl_tk=", "__cf_chl_f_tk=", "/cdn-cgi/challenge-platform/")):
        return True

    return False


def bring_browser_window_to_front(driver: Any) -> bool:
    """尝试将当前浏览器窗口置于操作系统最前台并获取焦点。"""
    success = False

    # 1. 尝试 Playwright 自身的 bring_to_front
    try:
        if hasattr(driver, "page") and hasattr(driver.page, "bring_to_front"):
            driver.page.bring_to_front()
            success = True
    except Exception as exc:
        logger.debug("[CF盾] page.bring_to_front 失败: %s", exc)

    # 2. 尝试 CDP 方式激活 Target 与窗口
    try:
        if hasattr(driver, "execute_cdp_cmd"):
            driver.execute_cdp_cmd("Target.activateTarget", {})
            driver.execute_cdp_cmd("Page.bringToFront", {})
            success = True
    except Exception as exc:
        logger.debug("[CF盾] CDP 激活窗口失败: %s", exc)

    # 3. Windows Win32 API 辅助置顶（遍历当前进程或 Chromium 窗体）
    try:
        import sys
        if sys.platform == "win32":
            user32 = ctypes.windll.user32
            found_hwnds = []
            # 尝试通过 Chrome 窗口类名定位
            def _enum_windows_cb(hwnd, extra):
                if user32.IsWindowVisible(hwnd):
                    length = user32.GetWindowTextLengthW(hwnd)
                    buff = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buff, length + 1)
                    title_text = buff.value.lower()
                    class_buff = ctypes.create_unicode_buffer(256)
                    user32.GetClassNameW(hwnd, class_buff, 256)
                    class_name = class_buff.value
                    if "Chrome_WidgetWin" in class_name or any(key in title_text for key in ("chatgpt", "moment", "chrome", "cloak", "google", "openai")):
                        # SW_RESTORE=9, SW_MAXIMIZE=3, SW_SHOW=5
                        user32.ShowWindow(hwnd, 9)
                        user32.SetForegroundWindow(hwnd)
                        found_hwnds.append(hwnd)
                return True

            enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_void_p)(_enum_windows_cb)
            user32.EnumWindows(enum_proc, 0)
            if found_hwnds:
                success = True
    except Exception as exc:
        logger.debug("[CF盾] Windows API 置顶窗口失败: %s", exc)

    return success


def _generate_bezier_points(x1: float, y1: float, x2: float, y2: float, steps: int = 15) -> list[tuple[float, float]]:
    """生成两点之间的拟真人类鼠标移动贝塞尔控制点。"""
    points = []
    # 随机控制点制造自然弧度
    cx1 = x1 + (x2 - x1) * random.uniform(0.2, 0.4) + random.uniform(-20, 20)
    cy1 = y1 + (y2 - y1) * random.uniform(0.1, 0.3) + random.uniform(-20, 20)
    cx2 = x1 + (x2 - x1) * random.uniform(0.6, 0.8) + random.uniform(-20, 20)
    cy2 = y1 + (y2 - y1) * random.uniform(0.7, 0.9) + random.uniform(-20, 20)

    for i in range(steps + 1):
        t = i / float(steps)
        # 三次贝塞尔曲线公式
        x = (1 - t) ** 3 * x1 + 3 * (1 - t) ** 2 * t * cx1 + 3 * (1 - t) * (t ** 2) * cx2 + (t ** 3) * x2
        y = (1 - t) ** 3 * y1 + 3 * (1 - t) ** 2 * t * cy1 + 3 * (1 - t) * (t ** 2) * cy2 + (t ** 3) * y2
        points.append((x, y))
    return points


def try_click_turnstile_cross_origin(driver: Any) -> bool:
    """尝试通过 Playwright FrameLocator 或 CDP 坐标穿透点击 Cloudflare Turnstile 复选框。"""
    prefix = getattr(driver, "_registration_log_prefix", "[CF盾]")

    # 方式一：Playwright Frame 级穿透（适用于 CloakBrowser）
    if hasattr(driver, "page") and hasattr(driver.page, "frames"):
        try:
            page = driver.page
            for frame in page.frames:
                f_url = str(getattr(frame, "url", "") or "")
                if "challenges.cloudflare.com" in f_url or "challenge-platform" in f_url:
                    for selector in (
                        "input[type='checkbox']",
                        "#challenge-stage",
                        ".ctp-checkbox-label",
                        "label.ctp-checkbox-label",
                        "span.mark",
                        "#cf-stage",
                    ):
                        loc = frame.locator(selector)
                        if loc.count() > 0 and loc.first.is_visible():
                            logger.info("%s 找到 Turnstile iframe 元素：%s，准备穿透模拟点击", prefix, selector)
                            time.sleep(random.uniform(0.4, 0.8))
                            loc.first.click()
                            time.sleep(random.uniform(0.6, 1.2))
                            return True
        except Exception as exc:
            logger.debug("%s Playwright Frame 穿透点击失败: %s", prefix, exc)

    # 方式二：坐标计算与 CDP 拟人鼠标穿透点击（通用兜底）
    try:
        box = driver.execute_script(r"""
        try {
            let target = document.querySelector('iframe[src*="challenges.cloudflare.com"], iframe[src*="challenge-platform"], iframe[title*="Cloudflare"], iframe[title*="Turnstile"], #challenge-stage iframe, iframe');
            if (!target) {
                target = document.querySelector('#challenge-stage, .ctp-checkbox-container, .ctp-checkbox-label');
            }
            if (!target) return null;
            const r = target.getBoundingClientRect();
            if (!r.width || !r.height) return null;
            // Turnstile 标准框中复选框在左侧水平约 28px，垂直居中
            const cbX = r.left + Math.min(30, r.width * 0.14);
            const cbY = r.top + r.height / 2;
            return {
                x: cbX,
                y: cbY,
                left: r.left,
                top: r.top,
                width: r.width,
                height: r.height,
                url: target.src || ''
            };
        } catch (e) {
            return null;
        }
        """)

        if box and box.get("x") and box.get("y"):
            target_x = float(box["x"]) + random.uniform(-3, 3)
            target_y = float(box["y"]) + random.uniform(-3, 3)
            logger.info("%s 探测到 Turnstile 控件绝对坐标: (%.1f, %.1f)，计算拟真鼠标轨迹", prefix, target_x, target_y)

            # 模拟从随机初始点移动到目标坐标
            start_x = target_x + random.uniform(-180, -60)
            start_y = target_y + random.uniform(-120, -40)
            points = _generate_bezier_points(start_x, start_y, target_x, target_y, steps=10)

            if hasattr(driver, "page") and hasattr(driver.page, "mouse"):
                # Playwright 原生仿真鼠标（最稳定，无 CDP 会话冲突）
                for px, py in points:
                    driver.page.mouse.move(px, py)
                    time.sleep(random.uniform(0.01, 0.03))
                time.sleep(random.uniform(0.08, 0.2))
                driver.page.mouse.click(target_x, target_y)
                logger.info("%s 已通过 Playwright Mouse 派发 Turnstile 穿透点击 (%.1f, %.1f)", prefix, target_x, target_y)
                time.sleep(random.uniform(0.8, 1.5))
                return True
            elif hasattr(driver, "execute_cdp_cmd"):
                for px, py in points:
                    driver.execute_cdp_cmd("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": px, "y": py})
                    time.sleep(random.uniform(0.01, 0.03))

                time.sleep(random.uniform(0.1, 0.25))
                driver.execute_cdp_cmd("Input.dispatchMouseEvent", {
                    "type": "mousePressed", "x": target_x, "y": target_y, "button": "left", "clickCount": 1
                })
                time.sleep(random.uniform(0.06, 0.15))
                driver.execute_cdp_cmd("Input.dispatchMouseEvent", {
                    "type": "mouseReleased", "x": target_x, "y": target_y, "button": "left", "clickCount": 1
                })
                logger.info("%s 已通过 CDP 派发 Turnstile 穿透点击", prefix)
                time.sleep(random.uniform(0.8, 1.5))
                return True
    except Exception as exc:
        logger.debug("%s CDP 坐标穿透点击失败: %s", prefix, exc)

    return False


def solve_or_wait_cloudflare_turnstile(
    driver: Any,
    *,
    timeout: int | None = None,
    max_auto_clicks: int | None = None,
    is_headless: bool = False,
    check_stop_cb: Callable[[], None] | None = None,
    on_relaunch_headed: Callable[[], Any] | None = None,
) -> bool:
    """全面解决 Cloudflare 盾：先自动尝试穿透点击，未通过时转人工接管并置顶等待。

    Returns:
        bool: True 表示页面已成功脱离 CF 盾并进入正常注册页；False 表示超时或失败。
    """
    if timeout is None:
        try:
            from config import cloakbrowser as _cb_cfg
            timeout = int(getattr(_cb_cfg, "CLOAK_HUMAN_TAKEOVER_TIMEOUT", 30) or 30)
        except Exception:
            timeout = 30

    if max_auto_clicks is None:
        try:
            from config import cloakbrowser as _cb_cfg
            max_auto_clicks = int(getattr(_cb_cfg, "CLOAK_AUTO_CLEAR_MAX_ATTEMPTS", 2) or 2)
        except Exception:
            max_auto_clicks = 2

    if not is_headless:
        is_headless = bool(getattr(driver, "_is_headless", False))
    if on_relaunch_headed is None:
        on_relaunch_headed = getattr(driver, "_relaunch_headed", None)

    prefix = getattr(driver, "_registration_log_prefix", "[CF盾]")

    # 1. 先检测是否触发了 Cloudflare 质询
    if not is_cloudflare_challenge(driver):
        return True

    logger.warning("%s 检测到页面处于 Cloudflare 质询盾（Just a moment...），启动自动清障与过盾流程", prefix)

    # 2. 阶段一：自动穿透点击尝试 (max_auto_clicks 次，静默无头清障)
    for attempt in range(1, max_auto_clicks + 1):
        if check_stop_cb:
            check_stop_cb()

        logger.info("%s 尝试在后台自动穿透点击清障（第 %d/%d 次）...", prefix, attempt, max_auto_clicks)
        clicked = try_click_turnstile_cross_origin(driver)
        if clicked:
            # 点击后等待 3-5 秒观察页面是否跳转
            for _ in range(8):
                if check_stop_cb:
                    check_stop_cb()
                time.sleep(0.5)
                if not is_cloudflare_challenge(driver):
                    logger.info("%s 自动清障点击成功，页面已顺利脱离 Cloudflare 质询！", prefix)
                    return True

        time.sleep(1.0)
        if not is_cloudflare_challenge(driver):
            logger.info("%s 页面已自动放行通过 Cloudflare 质询！", prefix)
            return True

    # 3. 阶段二：无头模式转有头（如果配置了重启回调）
    current_driver = driver
    if is_headless and on_relaunch_headed is not None:
        logger.warning("%s 无头模式自动清障未通过，正在切换为有头窗口模式弹出以供人工接管...", prefix)
        try:
            current_driver = on_relaunch_headed() or driver
            is_headless = False
        except Exception as exc:
            logger.error("%s 重启有头浏览器失败：%s，继续在原浏览器中尝试", prefix, exc)

    # 4. 阶段三：人工接管等待（有头模式窗口置顶 + 倒计时轮询）
    if not is_headless:
        bring_browser_window_to_front(current_driver)
        logger.warning(
            "%s [人工接管] 遇到 Cloudflare 人机验证，浏览器窗口已弹出并置顶！请在弹出的窗口中【手动勾选验证】（等待 %d 秒）...",
            prefix,
            timeout,
        )

    deadline = time.time() + timeout
    last_log_time = 0.0

    while time.time() < deadline:
        if check_stop_cb:
            check_stop_cb()

        if not is_cloudflare_challenge(current_driver):
            logger.info("%s [人工接管] 验证通过！页面已成功脱离 Cloudflare 盾，继续自动化注册流程！", prefix)
            return True

        remaining = int(math.ceil(deadline - time.time()))
        now = time.time()
        # 每隔 5 秒打印一次倒计时提示，保持日志清晰
        if now - last_log_time >= 5.0:
            if not is_headless:
                logger.info("%s [等待人工勾选] 请在弹出的浏览器中点击通过验证，剩余等待时间：%d 秒...", prefix, remaining)
                bring_browser_window_to_front(current_driver)
            else:
                logger.info("%s [等待页面放行] 页面仍在质询中，剩余等待时间：%d 秒...", prefix, remaining)
            last_log_time = now

        # 等待期间偶发重试一次穿透点击（有时 Turnstile iframe 加载慢）
        if int(now) % 6 == 0:
            try_click_turnstile_cross_origin(current_driver)

        time.sleep(1.5)

    logger.error("%s Cloudflare 人机验证等待超时（%d 秒），未能成功通过盾，即将放弃本账号并继续下一个", prefix, timeout)
    return False

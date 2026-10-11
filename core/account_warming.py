# -*- coding: utf-8 -*-
"""ChatGPT 账号拟人养号与后期维护引擎。

特性：
1. 纯指纹浏览器驱动（优先 CloakBrowser + Playwright，兼容 RoxyBrowser）。
2. StorageState 免密秒进：复用注册与历史登录态，失效时自动账密+2FA 补登并智能回填。
3. 三阶贝塞尔曲线人类鼠标移动模拟（Ghost-Cursor 算法），平滑自然轨迹与人手抖动。
4. 拟真键盘打字机：英文打字高斯抖动与退格修正，中文语义词组分块键入与构思停顿。
5. 多轮连贯交互树：按 IP 出口国家（英/中/日）强绑定语料画像，支持 1~2 轮自然追问。
6. 深度检测与自愈守卫：挂载 WhiteScreenGuard 白屏自愈、Cloudflare 自动过盾与封号协同标记。
"""
from __future__ import annotations

import json
import logging
import math
import random
import re
import time
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Any

from config import warming as _cfg
from core import db
from core.humanize import delay as human_delay
from core.white_screen_guard import is_white_screen
from core.cloudflare_turnstile import is_cloudflare_challenge, solve_or_wait_cloudflare_turnstile

logger = logging.getLogger(__name__)
_LOG_DIR = Path(__file__).resolve().parent.parent / "注册日志"


def _log_path(email: str) -> Path:
    safe = "".join(c if c.isalnum() or c in ".-_@" else "_" for c in str(email).strip().lower())
    return _LOG_DIR / f"warming_{safe}.log"


def _append_log(email: str, line: str, *, clear: bool = False) -> None:
    p = _log_path(email)
    p.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%H:%M:%S")
    mode = "w" if clear else "a"
    try:
        with p.open(mode, encoding="utf-8") as f:
            f.write(f"{stamp} [INFO] {line}\n")
    except Exception:
        pass


# ==============================================================================
# 1. 三阶贝塞尔曲线鼠标轨迹模拟 (Cubic Bézier Curve Trajectory)
# ==============================================================================

class BezierMouse:
    """模拟人类鼠标加速度、抖动与平滑轨迹。"""

    def __init__(self, page):
        self.page = page
        self.current_x = float(random.randint(200, 500))
        self.current_y = float(random.randint(150, 400))

    @staticmethod
    def _ease_in_out(t: float) -> float:
        """余弦加速度曲线，两端慢中间快。"""
        return 0.5 * (1.0 - math.cos(math.pi * t))

    def _generate_bezier_points(
        self,
        p0: tuple[float, float],
        p3: tuple[float, float],
        steps: int = 28,
    ) -> list[tuple[float, float]]:
        x0, y0 = p0
        x3, y3 = p3
        dx = x3 - x0
        dy = y3 - y0
        dist = math.hypot(dx, dy)

        spread = min(120.0, max(25.0, dist * 0.25))
        jitter_x1 = random.uniform(-spread, spread)
        jitter_y1 = random.uniform(-spread, spread)
        jitter_x2 = random.uniform(-spread, spread)
        jitter_y2 = random.uniform(-spread, spread)

        p1 = (x0 + dx * random.uniform(0.15, 0.45) + jitter_x1, y0 + dy * random.uniform(0.15, 0.45) + jitter_y1)
        p2 = (x0 + dx * random.uniform(0.55, 0.85) + jitter_x2, y0 + dy * random.uniform(0.55, 0.85) + jitter_y2)

        points = []
        for i in range(1, steps + 1):
            raw_t = i / float(steps)
            t = self._ease_in_out(raw_t)
            cx = (
                (1 - t) ** 3 * p0[0]
                + 3 * (1 - t) ** 2 * t * p1[0]
                + 3 * (1 - t) * t ** 2 * p2[0]
                + t ** 3 * p3[0]
            )
            cy = (
                (1 - t) ** 3 * p0[1]
                + 3 * (1 - t) ** 2 * t * p1[1]
                + 3 * (1 - t) * t ** 2 * p2[1]
                + t ** 3 * p3[1]
            )
            if i < steps:
                cx += random.uniform(-1.0, 1.0)
                cy += random.uniform(-1.0, 1.0)
            points.append((cx, cy))
        return points

    def move_to(self, target_x: float, target_y: float) -> None:
        p0 = (self.current_x, self.current_y)
        p3 = (float(target_x), float(target_y))
        dist = math.hypot(p3[0] - p0[0], p3[1] - p0[1])
        steps = max(15, min(45, int(dist / 22.0)))
        points = self._generate_bezier_points(p0, p3, steps=steps)

        for px, py in points:
            try:
                self.page.mouse.move(px, py)
            except Exception:
                pass
            time.sleep(random.uniform(0.005, 0.016))

        self.current_x = p3[0]
        self.current_y = p3[1]

    def click_element(self, locator) -> bool:
        try:
            box = locator.bounding_box()
            if not box:
                locator.click(timeout=3000)
                return True
            target_x = box["x"] + box["width"] * random.uniform(0.30, 0.70)
            target_y = box["y"] + box["height"] * random.uniform(0.30, 0.70)
            self.move_to(target_x, target_y)
            time.sleep(random.uniform(0.06, 0.16))
            self.page.mouse.down()
            time.sleep(random.uniform(0.05, 0.12))
            self.page.mouse.up()
            return True
        except Exception:
            try:
                locator.click(timeout=3000)
                return True
            except Exception:
                return False


# ==============================================================================
# 2. 语义词组分块拟人键盘打字机 (Semantic Chunk Typer)
# ==============================================================================

class SemanticChunkTyper:
    """模拟人类节奏打字，支持英文逐字符高斯微延迟与退格修正、中文语义词组分块输入与构思停顿。"""

    @staticmethod
    def _is_cjk(text: str) -> bool:
        return any("\u4e00" <= ch <= "\u9fff" or "\u3040" <= ch <= "\u30ff" for ch in text)

    @classmethod
    def _split_into_chunks(cls, text: str) -> list[str]:
        """将句子按标点与自然词组分块。中文切分成 2~4 字的自然词段，英文按单词切分。"""
        if not cls._is_cjk(text):
            # 英文按词或标点切分
            tokens = re.findall(r"\w+|[^\w\s]|\s+", text, re.UNICODE)
            return tokens or [text]

        # 中文语义分块：按标点先切句子，句子内按 2~4 字切分
        delimiters = "，。！？；、,.!?;:\n "
        chunks = []
        buf = []

        for ch in text:
            if ch in delimiters:
                if buf:
                    chunks.extend(cls._chunk_cjk_segment("".join(buf)))
                    buf = []
                chunks.append(ch)
            else:
                buf.append(ch)
        if buf:
            chunks.extend(cls._chunk_cjk_segment("".join(buf)))
        return chunks

    @staticmethod
    def _chunk_cjk_segment(seg: str) -> list[str]:
        res = []
        idx = 0
        n = len(seg)
        while idx < n:
            step = random.choice([2, 3, 4])
            res.append(seg[idx : idx + step])
            idx += step
        return res

    @classmethod
    def type_text(cls, page, text: str, wpm: int = 180) -> None:
        if not text:
            return

        base_delay = 60.0 / (max(80, min(320, wpm)) * 5)
        is_cjk_mode = cls._is_cjk(text)

        if not is_cjk_mode:
            # 纯英文模式：逐字符输入，支持偶发误触退格
            for char in text:
                if random.random() < 0.02 and char.isascii() and char.isalnum():
                    typo_char = random.choice("abcdefghijklmnopqrstuvwxyz")
                    try:
                        page.keyboard.type(typo_char)
                        time.sleep(random.uniform(0.12, 0.28))
                        page.keyboard.press("Backspace")
                        time.sleep(random.uniform(0.08, 0.18))
                    except Exception:
                        pass

                try:
                    page.keyboard.type(char)
                except Exception:
                    pass

                jitter = max(0.02, random.gauss(base_delay, base_delay * 0.35))
                time.sleep(jitter)

                if char in (",", ".", "?", "!", "\n"):
                    time.sleep(random.uniform(0.25, 0.55))
                elif char == " " and random.random() < 0.05:
                    # 5% 概率模拟中途轻微迟疑构思与重新组织语言停顿 (0.35s ~ 0.70s)
                    time.sleep(random.uniform(0.35, 0.70))
        else:
            # 中文/日文 CJK 模式：语义词组分块键入
            chunks = cls._split_into_chunks(text)
            for chunk in chunks:
                if not chunk:
                    continue

                # 标点符号直接输入并停顿
                if chunk in ("，", "。", "！", "？", "；", "、", "\n", ",", ".", "?", "!"):
                    try:
                        page.keyboard.type(chunk)
                    except Exception:
                        pass
                    time.sleep(random.uniform(0.30, 0.65))
                    continue

                # 2% 概率模拟词组级轻度手抖打错并退格撤回
                if random.random() < 0.02 and len(chunk) >= 2:
                    typo_chunk = random.choice(["不", "想", "这", "对", "请", "能"])
                    try:
                        page.keyboard.type(typo_chunk)
                        time.sleep(random.uniform(0.20, 0.35))
                        page.keyboard.press("Backspace")
                        time.sleep(random.uniform(0.12, 0.22))
                    except Exception:
                        pass

                # 词组内流畅键入
                for char in chunk:
                    try:
                        page.keyboard.type(char)
                    except Exception:
                        pass
                    # 词内字间微小停顿 (0.04s ~ 0.09s)
                    char_jitter = max(0.025, random.gauss(base_delay * 0.7, base_delay * 0.2))
                    time.sleep(char_jitter)

                # 词组间的人类呼吸与构思停顿 (0.15s ~ 0.40s)，5% 概率偶发更深思考停顿 (0.45s ~ 0.85s)
                if random.random() < 0.05:
                    time.sleep(random.uniform(0.45, 0.85))
                else:
                    time.sleep(random.uniform(0.15, 0.40))



HumanTyper = SemanticChunkTyper


# ==============================================================================
# 3. 本地多语言语料库与连贯追问树
# ==============================================================================

def _resolve_language_by_country(country: str | None) -> str:
    """按账号绑定的出口 IP 国家代码解析最佳交互语言。
    
    规则：
    - US/UK/CA/AU 等英语国家：100% 采用地道口语化英文 ('en')
    - JP：采用敬语与自然日语 ('ja')
    - TW/HK/MO：采用繁体中文或地道日常中文表达 ('zh_tw')
    - CN：采用地道自然简体中文 ('zh')
    """
    c = str(country or "").strip().upper()
    if c in {"US", "GB", "UK", "CA", "AU", "NZ", "IE", "DE", "FR", "NL", "SE", "SG"}:
        return "en"
    if c == "JP":
        return "ja"
    if c in {"TW", "HK", "MO"}:
        return "zh_tw"
    if c in {"CN"}:
        return "zh"
    return "en"


def load_random_prompt_tree(country: str | None = None) -> tuple[str, str, str, list[str], list[str]]:
    """从本地语料库根据 Persona Matrix 与 IP 语言随机抽取提问与 1~3 轮连贯对话树。
    
    返回：(language, persona_category, prompt, followups, closings)
    """
    corpus_rel = getattr(_cfg, "WARMING_CORPUS_FILE", "config/prompts_corpus.json")
    corpus_path = Path(__file__).resolve().parent.parent / corpus_rel
    target_lang = _resolve_language_by_country(country)

    default_persona_weights = {
        "workplace_efficiency": 25,       # 职场白领 / 效率工具人 (25%)
        "student_lifelong_learning": 25,  # 在校学生 / 终身学习者 (25%)
        "daily_lifestyle": 20,            # 日常技术生活探索者 (20%)
        "programmer_geek": 15,            # 程序员 / 极客 (15%)
        "travel_and_culture": 15,         # 旅行与泛文化爱好者 (15%)
    }
    persona_weights = getattr(_cfg, "PERSONA_WEIGHTS", default_persona_weights) or default_persona_weights

    try:
        if corpus_path.exists():
            data = json.loads(corpus_path.read_text(encoding="utf-8"))
            languages = data.get("languages") or {}
            lang_dict = languages.get(target_lang) or languages.get("en") or languages.get("zh") or {}

            if lang_dict:
                available_categories = list(lang_dict.keys())
                weights = [persona_weights.get(cat, 15) for cat in available_categories]
                chosen_cat = random.choices(available_categories, weights=weights, k=1)[0]
                items = lang_dict.get(chosen_cat) or []
                if items:
                    picked = random.choice(items)
                    generic_followups = (data.get("generic_followups") or {}).get(target_lang, [])
                    generic_closings = (data.get("generic_closings") or {}).get(target_lang, [])

                    if isinstance(picked, dict):
                        p = picked.get("prompt") or ""
                        f = list(picked.get("followups") or []) or generic_followups
                        c = list(picked.get("closings") or []) or generic_closings
                        return target_lang, chosen_cat, p, f, c
                    elif isinstance(picked, str):
                        return target_lang, chosen_cat, picked, generic_followups, generic_closings

            categories = data.get("categories") or {}
            if categories:
                cat_name = random.choice(list(categories.keys()))
                prompts = categories[cat_name]
                if prompts:
                    p = random.choice(prompts)
                    return target_lang, cat_name, p, [], []
    except Exception as exc:
        logger.warning("[Warming] 加载本地语料库失败，使用内置兜底题库: %s", exc)

    fallback_en = [
        ("workplace_efficiency", "Could you help polish this project update email to our VP to sound more concise and confident?", [
            "Can you turn the key deliverables and deadlines into a clean bulleted table?",
            "Could you elaborate on the second point regarding mitigation for the delay?"
        ], ["Looks great, thank you so much!", "Got it, that's super helpful. Thanks!"]),
        ("student_lifelong_learning", "Can you explain the Sunk Cost Fallacy using an intuitive everyday life example?", [
            "Could you elaborate on how this bias shows up in career decisions?",
            "Can you summarize this into 3 practical self-check questions?"
        ], ["Really clear explanation, thank you!", "That makes complete sense, thanks!"]),
        ("daily_lifestyle", "What are 3 quick and healthy dinner ideas that can be prepared in under 25 minutes?", [
            "Could you give me the step-by-step cooking steps for the first one?",
            "Can any of these be prepped the night before for quick assembly?"
        ], ["Thanks, making this tonight!", "Sounds delicious and easy, thanks!"]),
        ("programmer_geek", "What is the cleanest regex to match valid semver strings with optional pre-release tags?", [
            "Can you explain what each capture group does in that pattern?",
            "Could you show how to extract the major, minor, and patch in Python?"
        ], ["Regex tested and works, thanks!", "Clear breakdown, appreciate the regex explanation."]),
        ("travel_and_culture", "What are the essential packing and route planning tips for a 2-day weekend mountain hike?", [
            "What kind of emergency and blister-care items should never be skipped?",
            "Could you turn the gear list into a checklist grouped by category?"
        ], ["Packing right now, thanks for the guide!", "Very thorough advice, thank you!"])
    ]
    fallback_zh_tw = [
        ("workplace_efficiency", "請幫我潤色一封寄給副總的專案進度回報信件，希望語氣簡潔專業且展現掌控力。", [
            "能不能把核心里程碑與截止日期整理成一張簡短的核對清單？",
            "第二點關於潛在風險因應的部分，能再精簡為兩句話嗎？"
        ], ["修訂得非常得體，非常感謝！", "好的，這樣回報清晰多了，謝謝你的幫忙！"]),
        ("student_lifelong_learning", "請用通俗易懂的生活例子，向初學者解釋什麼是『沉沒成本謬誤』。", [
            "這個心理偏誤在日常職涯或感情選擇中常見的具體表現是什麼？",
            "能不能歸納出 3 個幫助自己及時停損的自問清單？"
        ], ["解釋得非常生動，完全懂了！", "非常有啟發，感謝詳細的說明。"]),
        ("daily_lifestyle", "套房室內採光普通，想挑幾盆耐陰、不易爛根且對貓咪無毒的室內植物有哪些？", [
            "像波士頓腎蕨或吊蘭在秋冬季大約幾天澆一次水比較安全？",
            "有沒有不靠濕度計就能判斷土壤乾燥程度的小撇步？"
        ], ["家裡有貓真的很需要注意這個，太感謝了！", "週末就去花市挑一盆，謝謝！"])
    ]
    fallback_zh = [
        ("workplace_efficiency", "帮我润色一段给项目总监的周报更新邮件，希望语气精炼专业，突出关键进展与风险把控。", [
            "能否把核心成果与下周待办梳理成一张简洁的对照表格？",
            "针对延期风险的应对措施，能再精简提炼成两句话吗？"
        ], ["润色得很得体，多谢！", "好的，非常清晰，谢谢！"]),
        ("student_lifelong_learning", "请用通俗易懂的生活例子，向初学者解释一下什么是‘沉没成本谬误’。", [
            "能不能总结出 3 个在做日常决策时避免沉没成本陷阱的自查要点？",
            "在学业或职业转型面临沉没成本时，如何理性建立止损思维？"
        ], ["举例很形象，完全理解了！", "非常有启发，感谢解答！"]),
        ("daily_lifestyle", "请推荐几道简单、营养又好做的家常快手菜，最好半小时内能搞定且适合带便当。", [
            "请详细说说第一道菜的具体调料配比和烹饪火候要点。",
            "第二天下便当用微波炉加热时，怎样才能尽量保持口感不发软？"
        ], ["今晚就照着食谱试做一下，谢谢！", "好的，非常实用，感谢分享！"])
    ]
    fallback_ja = [
        ("workplace_efficiency", "本日の打ち合わせの御礼と、来週火曜日までに進捗共有をお送りする丁寧なビジネスメールを作成してください。", [
            "主要な決定事項3点を簡潔な箇条書きフォーマットにまとめていただけますか？",
            "もう少し柔らかく誠意が伝わるトーンに微調整してもらえますか？"
        ], ["完璧です。迅速な作成をありがとうございました！", "大変分かりやすく助かりました。ありがとうございます。"])
    ]

    pool = fallback_zh_tw if target_lang == "zh_tw" else (fallback_ja if target_lang == "ja" else (fallback_zh if target_lang == "zh" else fallback_en))
    cat, p, f, c = random.choice(pool)
    return target_lang, cat, p, f, c


def load_random_prompt(country: str | None = None) -> tuple[str, str]:
    """兼容旧接口调用方：返回 (category, prompt)。"""
    res = load_random_prompt_tree(country=country)
    return res[1], res[2]


# ==============================================================================
# 4. 养号执行核心服务
# ==============================================================================

class AccountWarmingService:
    """单个账号拟人养号完整生命周期。"""

    def __init__(
        self,
        account_data: dict,
        *,
        headless: bool | None = None,
        enable_dialogue: bool | None = None,
        dialogue_turns: int | None = None,
    ):
        self.account = dict(account_data)
        self.email = str(self.account.get("email") or "").strip()
        self.acc_id = int(self.account.get("id") or 0)
        self.headless = bool(getattr(_cfg, "WARMING_HEADLESS", False)) if headless is None else bool(headless)
        self.enable_dialogue = bool(getattr(_cfg, "WARMING_ENABLE_DIALOGUE", False)) if enable_dialogue is None else bool(enable_dialogue)
        self.dialogue_turns = int(getattr(_cfg, "WARMING_DIALOGUE_TURNS", 2) or 2) if dialogue_turns is None else int(dialogue_turns)
        raw_c = str(self.account.get("country") or "").strip().upper()
        _c_map = {
            "UNITED STATES": "US", "USA": "US", "AMERICA": "US",
            "JAPAN": "JP",
            "GERMANY": "DE",
            "UNITED KINGDOM": "GB", "UK": "GB",
            "FI": "US", "FINLAND": "US",
        }
        self.country = _c_map.get(raw_c, raw_c)
        self.driver = None
        self.page = None
        self.mouse = None

    def _resolve_proxy(self) -> str | None:
        """根据核心 3 级容灾策略解析代理（Level 1 历史绑定 -> Level 2 同国备用 -> Level 3 同洲递归 -> Fail-Safe）。"""
        try:
            from core.continent_proxy_router import resolve_account_proxy_3tier
            proxy_url, target_country, tier = resolve_account_proxy_3tier(
                self.account, check_alive=True
            )
            self.country = target_country
            tier_names = {
                "level1_sticky": "Level 1 历史粘性连接",
                "level2_same_country": "Level 2 同国备用出口",
                "level3_same_continent": "Level 3 同大洲递归备用",
            }
            _append_log(self.email, f"[代理] 调度成功 [{tier_names.get(tier, tier)}]: {proxy_url} (国家: {target_country})")
            return proxy_url
        except Exception as exc:
            _append_log(self.email, f"[代理] 三级容灾调度拦截: {exc}")
            raise

    def _init_browser(self) -> None:
        """启动指纹浏览器并注入 StorageState 登录态。"""
        proxy = self._resolve_proxy()
        storage_state = self.account.get("storage_state")
        driver_type = str(getattr(_cfg, "WARMING_DRIVER", "cloak") or "cloak").strip().lower()

        _append_log(
            self.email,
            f"[浏览器] 正在启动指纹浏览器 (驱动={driver_type}, headless={self.headless}, 有免登态={bool(storage_state)})...",
        )

        if driver_type == "roxy":
            try:
                from core.roxybrowser_client import open_roxy_browser
                self.driver = open_roxy_browser(proxy=proxy, headless=self.headless)
                self.page = getattr(self.driver, "page", None)
            except Exception as r_err:
                _append_log(self.email, f"[浏览器] 启动 Roxy 驱动失败，自动回退到 CloakBrowser: {r_err}")
                driver_type = "cloak"

        if driver_type != "roxy" or not self.page:
            from core.cloakbrowser_driver import build_cloak_driver
            from core.fingerprint import resolve_account_fingerprint_seed
            fp_seed = resolve_account_fingerprint_seed(self.account, email=self.email)
            _append_log(
                self.email,
                f"[浏览器] 匹配账号专属指纹环境: profile=cloak_{fp_seed} (seed={fp_seed})",
            )
            self.driver, _ = build_cloak_driver(
                proxy=proxy,
                country=self.country or None,
                storage_state=storage_state if isinstance(storage_state, dict) else None,
                headless=self.headless,
                fingerprint_seed=fp_seed,
            )
            self.page = getattr(self.driver, "page", None)

        if not self.page:
            raise RuntimeError("浏览器启动失败，无法获取 page 实例")
        self.mouse = BezierMouse(self.page)

        # 深度省流：挂载可选静态资源与高频监控遥测拦截器
        self.data_saver = None
        try:
            from core.browser_data_saver import BrowserDataSaver
            self.data_saver = BrowserDataSaver(label=f"Warming-{self.email}")
            context = getattr(self.page, "context", None)
            if context:
                self.data_saver.install_playwright(context)
                _append_log(self.email, "[省流] 已激活浏览器深度省流模式 (拦截图片/字体/遥测监控)")
            elif self.driver:
                self.data_saver.install_selenium(self.driver)
        except Exception as ds_err:
            logger.debug("[Warming] 安装省流量模式异常: %s", ds_err)

    def _guard_page(self) -> None:
        """执行白屏检测自愈与 Cloudflare 质询盾检测。"""
        # 0. 检测 chrome-error 代理连接失败
        try:
            curr_url = getattr(self.page, "url", "") or ""
            if "chrome-error://" in curr_url or not curr_url or curr_url == "about:blank":
                _append_log(self.email, f"[守卫] 页面出现网络错误/代理连接受阻 ({curr_url[:60]})，尝试刷新恢复...")
                try:
                    self.page.goto("https://chatgpt.com", wait_until="domcontentloaded", timeout=20000)
                except Exception:
                    pass
                time.sleep(2.0)
        except Exception:
            pass

        # 1. 检测 Cloudflare 质询盾
        try:
            if is_cloudflare_challenge(self.driver):
                _append_log(self.email, "[守卫] 检测到 Cloudflare 人机质询盾，正在尝试自动穿透...")
                solve_or_wait_cloudflare_turnstile(self.driver, timeout=25.0)
        except Exception as exc:
            logger.debug("[Warming] 质询盾检测异常: %s", exc)

        # 2. 检测页面白屏
        try:
            if is_white_screen(self.driver):
                _append_log(self.email, "[守卫] 检测到页面持续空白，立即触发刷新自愈...")
                self.page.reload(wait_until="domcontentloaded", timeout=20000)
                time.sleep(3.0)
                if is_cloudflare_challenge(self.driver):
                    solve_or_wait_cloudflare_turnstile(self.driver, timeout=20.0)
        except Exception as exc:
            logger.debug("[Warming] 白屏检测异常: %s", exc)

    def _check_session_alive(self) -> tuple[bool, str | None]:
        """检测当前 ChatGPT 登录态及是否遭遇封禁。
        
        返回：(is_alive, reason)
        reason 为 'deactivated' 说明账号已封禁，'session_expired' 为普通失效。
        """
        self._guard_page()
        curr_url = getattr(self.page, "url", "") or ""
        if "chatgpt.com" not in curr_url:
            return False, "not_chatgpt_origin"

        try:
            script = """async () => {
                try {
                    const ctrl = new AbortController();
                    const timer = setTimeout(() => ctrl.abort(), 8000);
                    const res = await fetch('/api/auth/session', {credentials: 'include', signal: ctrl.signal});
                    clearTimeout(timer);
                    const status = res.status;
                    let text = '';
                    try { text = await res.text(); } catch(e){}
                    let j = {};
                    try { j = JSON.parse(text); } catch(e){}
                    const isHtml = text.trim().startsWith('<');
                    const isDeactivated = !isHtml && (text.includes('account_deactivated') || text.includes('deactivated'));
                    const isCf = isHtml || text.includes('Just a moment') || text.includes('cloudflare');
                    return {
                        ok: res.ok,
                        status: status,
                        is_cf: isCf,
                        has_token: !!(j && j.accessToken),
                        access_token: (j && j.accessToken) ? j.accessToken : null,
                        user: j && j.user ? j.user.email : null,
                        deactivated: isDeactivated,
                        raw: text.slice(0, 300)
                    };
                } catch (e) {
                    return {ok: false, error: String(e)};
                }
            }"""
            data = self.page.evaluate(script)
            if isinstance(data, dict):
                if data.get("deactivated"):
                    _append_log(self.email, f"[Session] ⚠️ 检测到账号已被 OpenAI 停用/封禁 (HTTP {data.get('status')}): {data.get('raw')}")
                    return False, "deactivated"
                if data.get("is_cf"):
                    return False, "cloudflare"
                if data.get("ok") and data.get("has_token"):
                    at = data.get("access_token")
                    if at:
                        self.latest_access_token = at
                        self.account["access_token"] = at
                    _append_log(self.email, f"[Session] 免密登录态有效 (用户: {data.get('user')})")
                    return True, None
        except Exception as exc:
            logger.debug("[Warming] Session 检查异常: %s", exc)

        # 检查页面是否有被封禁的可见文字
        try:
            page_text = self.page.evaluate("() => document.body ? (document.body.innerText || '') : ''")
            if any(term in page_text for term in ["Your account has been deleted or deactivated", "account_deactivated", "Your account is not active"]):
                _append_log(self.email, "[页面] 发现封号提示文字：Your account has been deactivated")
                return False, "deactivated"
        except Exception:
            pass

        # 检查浏览器 Cookie 中是否已具备有效 session-token 凭据
        try:
            context = getattr(self.driver, "context", None)
            if not context and hasattr(self.page, "context"):
                context = self.page.context
            if context:
                cookies = context.cookies()
                has_sess_cookie = any(
                    "session-token" in str(c.get("name", "")).lower() and len(str(c.get("value", ""))) > 20
                    for c in cookies
                )
                if has_sess_cookie and getattr(self, "latest_access_token", None):
                    _append_log(self.email, "[Session] 检测到有效 session-token Cookie 凭据与最新 AccessToken，判定为免密登录态有效")
                    return True, None
        except Exception:
            pass

        return False, "session_expired"

    def _setup_or_reset_password(self) -> str | None:
        """为尚未持有 OpenAI 密码的账号通过官方 Forgot Password 邮件重置流程全自动设定并绑定新密码。"""
        _append_log(self.email, "[密码自愈] 检测到账号未设置 OpenAI 密码，尝试通过 Forgot Password 自动设定...")
        try:
            # 1. 寻找 Forgot password 链接并点击
            forgot_btn = self.page.locator(
                "a:has-text('Forgot password?'), a:has-text('Forgot password'), "
                "button:has-text('Forgot password'), a:has-text('忘记密码'), "
                "a:has-text('パスワードをお忘れですか'), a[href*='forgot-password'], a[href*='reset']"
            ).first

            clicked = False
            if forgot_btn.is_visible(timeout=2000):
                self.mouse.click_element(forgot_btn)
                clicked = True
            else:
                try:
                    clicked = self.page.evaluate("""() => {
                        const els = Array.from(document.querySelectorAll('a, button'));
                        for (const el of els) {
                            const t = (el.innerText || el.textContent || '').trim().toLowerCase();
                            if (t.includes('forgot') || t.includes('忘记密码') || t.includes('お忘れ') || t.includes('vergessen')) {
                                el.click();
                                return true;
                            }
                        }
                        return false;
                    }""")
                except Exception:
                    pass

            if not clicked:
                try:
                    self.page.goto("https://auth.openai.com/forgot-password", wait_until="domcontentloaded", timeout=20000)
                except Exception:
                    pass

            time.sleep(2.5)
            self._guard_page()

            # 2. 在 Forgot Password 页面填入邮箱并提交
            email_input = self.page.locator("input[type='email'], input[name='email'], input[name='username']").first
            if email_input.is_visible(timeout=3000):
                self.mouse.click_element(email_input)
                try:
                    self.page.keyboard.press("Control+A")
                    self.page.keyboard.press("Backspace")
                except Exception:
                    pass
                self.page.keyboard.type(self.email, delay=30)

            req_time = time.time()
            continue_btn = self.page.locator(
                "button:has-text('Continue'), button:has-text('Reset password'), button:has-text('继续'), button:has-text('送信'), button[type='submit']"
            ).first
            if continue_btn.is_visible(timeout=3000):
                self.mouse.click_element(continue_btn)
                time.sleep(3.0)
                self._guard_page()

            # 3. 轮询收件箱抓取重置密码邮件链接
            _append_log(self.email, "[密码自愈] 已触发密码重置邮件，正在从收件箱轮询抓取重置链接...")
            from core.email_provider import wait_for_password_reset_link
            reset_url = wait_for_password_reset_link(self.email, after_ts=req_time - 5.0, max_wait=45, email_source=self.account.get("email_source"))
            _append_log(self.email, f"[密码自愈] 成功获取密码重置安全链接，正在跳转设定新密码...")

            # 4. 浏览器跳转进入重置链接
            self.page.goto(reset_url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(3.0)
            self._guard_page()

            # 5. 生成强随机密码
            from core.auth_flow.password_step import _registration_password
            new_pwd = _registration_password()

            # 6. 填入新密码
            pwd_box = self.page.locator("input[type='password'], input[name='password'], input[name='newPassword']").first
            if pwd_box.is_visible(timeout=6000):
                self.mouse.click_element(pwd_box)
                self.page.keyboard.type(new_pwd, delay=25)

            confirm_box = self.page.locator("input[name='confirmPassword'], input[name='password_confirmation'], input[name='confirm_password']").first
            if confirm_box.is_visible(timeout=1500):
                self.mouse.click_element(confirm_box)
                self.page.keyboard.type(new_pwd, delay=25)

            submit_reset = self.page.locator(
                "button:has-text('Reset password'), button:has-text('Save'), button:has-text('重置密码'), button:has-text('Continue'), button[type='submit']"
            ).first
            if submit_reset.is_visible(timeout=3000):
                self.mouse.click_element(submit_reset)
                time.sleep(3.0)
                self._guard_page()

            # 7. 永久写入数据库
            db.update_account_registration_password(self.acc_id, new_pwd)
            self.account["registration_password"] = new_pwd
            _append_log(self.email, f"[密码自愈] ✅ 密码设定成功并持久化回填至 SQLite！(长度 {len(new_pwd)})")
            return new_pwd
        except Exception as exc:
            _append_log(self.email, f"[密码自愈] 自动设置密码流程未能完成: {exc}，将平滑回退常规验证码通道")
            logger.debug("[Warming] 设置密码异常: %s", exc)
            return None

    def _try_relogin(self) -> bool:
        """当 Session 失效或首次无 StorageState 时，执行智能登录自愈补登（支持密码、邮箱验证码 OTP 及 TOTP 2FA），并捕获回填。"""
        _append_log(self.email, "[登录] 正在执行智能自愈补登...")
        password = str(
            self.account.get("registration_password")
            or db._extract_registration_password(self.account)
            or ""
        ).strip()
        totp_secret = str(
            self.account.get("totp_secret")
            or (self.account.get("extra_json") if isinstance(self.account.get("extra_json"), dict) else {}).get("totp_secret")
            or ""
        ).strip()
        email_source = self.account.get("email_source")

        try:
            self._guard_page()
            curr_url = getattr(self.page, "url", "") or ""

            # 1. 确保进入 chatgpt.com 域以获取 NextAuth CSRF 与发起 OAuth 授权
            if "chatgpt.com" not in curr_url or any(k in curr_url for k in ("/auth/error", "/auth/login")):
                try:
                    self.page.goto("https://chatgpt.com", wait_until="domcontentloaded", timeout=25000)
                except Exception as e:
                    logger.debug("[Warming] 导航 chatgpt.com 提示: %s", e)
                time.sleep(2.5)
                self._guard_page()

            # 2. 优先通过 NextAuth 触发官方 OAuth 流程（避免前端表单直填触发 /auth/error）
            import uuid
            did = str(uuid.uuid4())
            auth_log_id = str(uuid.uuid4())
            reauth_start_ts = time.time()
            _append_log(self.email, f"[登录] 正在发起 NextAuth 登录授权: {self.email} ...")

            signin_res = None
            try:
                signin_res = self.page.evaluate("""async ({email, did, authLogId}) => {
                    try {
                        const ctrl1 = new AbortController();
                        const timer1 = setTimeout(() => ctrl1.abort(), 8000);
                        const csrfResp = await fetch('/api/auth/csrf', {credentials: 'include', signal: ctrl1.signal});
                        clearTimeout(timer1);
                        const csrfData = await csrfResp.json();
                        const csrfToken = csrfData.csrfToken;
                        if (!csrfToken) return {ok: false, error: 'no_csrf_token'};

                        const q = new URLSearchParams({
                            prompt: 'login',
                            'ext-oai-did': did,
                            auth_session_logging_id: authLogId,
                            'ext-passkey-client-capabilities': '11111',
                            screen_hint: 'login_or_signup',
                            login_hint: email
                        });
                        const body = new URLSearchParams({
                            callbackUrl: 'https://chatgpt.com/',
                            csrfToken: csrfToken,
                            json: 'true'
                        });
                        const ctrl2 = new AbortController();
                        const timer2 = setTimeout(() => ctrl2.abort(), 10000);
                        const resp = await fetch('/api/auth/signin/openai?' + q.toString(), {
                            method: 'POST',
                            credentials: 'include',
                            headers: {'Content-Type': 'application/x-www-form-urlencoded'},
                            body: body.toString(),
                            signal: ctrl2.signal
                        });
                        clearTimeout(timer2);
                        const data = await resp.json();
                        return {ok: true, url: data.url};
                    } catch(e) {
                        return {ok: false, error: String(e)};
                    }
                }""", {"email": self.email, "did": did, "authLogId": auth_log_id})
            except Exception as eval_err:
                logger.debug("[Warming] NextAuth signin evaluate 异常: %s", eval_err)

            if isinstance(signin_res, dict) and signin_res.get("ok") and signin_res.get("url"):
                auth_url = signin_res["url"]
                _append_log(self.email, "[登录] NextAuth 授权完成，跳转至 auth.openai.com 认证页...")
                try:
                    self.page.goto(auth_url, wait_until="domcontentloaded", timeout=30000)
                except Exception as goto_err:
                    logger.debug("[Warming] 导航 auth_url 提示: %s", goto_err)
                time.sleep(3.0)
                self._guard_page()
            else:
                # 备选：UI 方式进入
                _append_log(self.email, f"[登录] NextAuth 授权未直接返回 URL ({signin_res})，尝试常规 UI 登录入口...")
                login_btn = self.page.locator(
                    "button:has-text('Log in'), a:has-text('Log in'), button:has-text('登录'), a:has-text('登录'), a[href*='/auth/login'], button[data-testid='login-button']"
                ).first
                if login_btn.is_visible(timeout=3000):
                    self.mouse.click_element(login_btn)
                    time.sleep(2.5)
                    self._guard_page()
                else:
                    try:
                        self.page.goto("https://chatgpt.com/auth/login", wait_until="domcontentloaded", timeout=15000)
                    except Exception:
                        pass
                    time.sleep(3.0)
                    self._guard_page()

                email_input = self.page.locator("input[type='email'], input[name='username'], input[name='email'], input#email-input").first
                if email_input.is_visible(timeout=8000):
                    _append_log(self.email, f"[登录] 填入登录邮箱: {self.email}")
                    self.mouse.click_element(email_input)
                    try:
                        self.page.keyboard.press("Control+A")
                        self.page.keyboard.press("Backspace")
                    except Exception:
                        pass
                    self.page.keyboard.type(self.email, delay=35)
                    human_delay("click", minimum=0.4, maximum=0.8)
                    try:
                        self.page.keyboard.press("Enter")
                    except Exception:
                        pass
                    reauth_start_ts = time.time()
                    continue_btn = self.page.locator("button:has-text('Continue'), button:has-text('继续'), button[type='submit']").first
                    if continue_btn.is_visible(timeout=4000):
                        self.mouse.click_element(continue_btn)
                        time.sleep(3.0)
                        self._guard_page()

            # 进入状态流转自愈循环（覆盖 CF 盾、密码、邮箱验证码、TOTP 2FA 及主站回跳，最长 180s）
            loop_start = time.time()
            otp_handled = False
            pwd_handled = False
            totp_handled = False
            last_totp_attempt_ts = 0.0
            totp_attempts = 0
            last_stage_log_time = 0.0
            consecutive_chrome_errors = 0

            while time.time() - loop_start < 180:
                self._guard_page()
                curr_url = getattr(self.page, "url", "") or ""
                if "chrome-error://" in curr_url:
                    consecutive_chrome_errors += 1
                    if consecutive_chrome_errors >= 4:
                        _append_log(self.email, f"[网络] 页面连续多次遭遇代理/网络阻断 ({curr_url[:60]})，当前出口异常，中止当前流程")
                        return False
                else:
                    consecutive_chrome_errors = 0

                curr_page_text = self.page.evaluate("() => document.body ? (document.body.innerText || '') : ''")

                # 0.5 异常授权页快速脱困 (/auth/error)
                if "/auth/error" in curr_url or "api/auth/error" in curr_url:
                    _append_log(self.email, f"[登录] 检测到授权冲突/错误页 ({curr_url[:60]})，自动清理旧会话并重新拉起登录入口...")
                    try:
                        context = getattr(self.driver, "context", None) or getattr(self.page, "context", None)
                        if context:
                            context.clear_cookies()
                        self.page.goto("https://chatgpt.com/auth/login", wait_until="domcontentloaded", timeout=20000)
                    except Exception as nav_err:
                        logger.debug("[Warming] 脱困导航提示: %s", nav_err)
                    time.sleep(3.0)
                    self._guard_page()
                    continue

                # 1. 成功回跳 ChatGPT 主站并验证 Session
                if "chatgpt.com" in curr_url and not any(k in curr_url for k in ("auth.openai.com", "/auth/login", "/auth/error")):
                    alive, reason = self._check_session_alive()
                    if alive:
                        _append_log(self.email, "[登录] 自动重登成功！已捕获最新 StorageState 并永久回填")
                        try:
                            context = getattr(self.driver, "context", None)
                            if not context and hasattr(self.page, "context"):
                                context = self.page.context
                            if context:
                                new_state = context.storage_state()
                                self.account["storage_state"] = new_state
                                db.update_account_storage_state(self.acc_id, new_state)
                        except Exception as exc:
                            logger.debug("[Warming] 导出 StorageState 异常: %s", exc)
                        return True
                    if reason == "deactivated":
                        raise RuntimeError("账号已被封禁或停用 (deactivated)")
                    if not (otp_handled or totp_handled or pwd_handled):
                        # 如果掉回主页未登录，优先重新触发 NextAuth 快速切入 auth.openai.com
                        try:
                            reauth_res = self.page.evaluate("""async ({email, did, authLogId}) => {
                                try {
                                    const csrfResp = await fetch('/api/auth/csrf', {credentials: 'include'});
                                    const csrfData = await csrfResp.json();
                                    const csrfToken = csrfData && csrfData.csrfToken;
                                    if (!csrfToken) return null;
                                    const q = new URLSearchParams({
                                        prompt: 'login',
                                        'ext-oai-did': did,
                                        auth_session_logging_id: authLogId,
                                        'ext-passkey-client-capabilities': '11111',
                                        screen_hint: 'login_or_signup',
                                        login_hint: email
                                    });
                                    const body = new URLSearchParams({
                                        callbackUrl: 'https://chatgpt.com/',
                                        csrfToken: csrfToken,
                                        json: 'true'
                                    });
                                    const resp = await fetch('/api/auth/signin/openai?' + q.toString(), {
                                        method: 'POST',
                                        credentials: 'include',
                                        headers: {'Content-Type': 'application/x-www-form-urlencoded'},
                                        body: body.toString()
                                    });
                                    const data = await resp.json();
                                    return data && data.url ? data.url : null;
                                } catch(e) { return null; }
                            }""", {"email": self.email, "did": did, "authLogId": auth_log_id})
                            if reauth_res:
                                _append_log(self.email, "[登录] 主页 NextAuth 快速重定向至 auth.openai.com...")
                                self.page.goto(reauth_res, wait_until="domcontentloaded", timeout=25000)
                                time.sleep(3.0)
                                continue
                        except Exception:
                            pass

                        login_btn = self.page.locator("button[data-testid='login-button'], a[href*='/auth/login'], button:has-text('Log in'), button:has-text('登录')").first
                        if login_btn.is_visible(timeout=1000):
                            _append_log(self.email, "[登录] 处于主页未登录状态，点击 Log in 按钮进入登录页...")
                            self.mouse.click_element(login_btn)
                            time.sleep(3.0)
                            continue

                # 2. 封号提示检测
                if any(term in curr_page_text for term in ["Your account has been deleted or deactivated", "account_deactivated", "Your account is not active"]):
                    _append_log(self.email, "[页面] 发现封号提示：Your account has been deactivated")
                    raise RuntimeError("账号已被封禁或停用 (deactivated)")

                # 2.5 邮箱输入阶段（如果处于 auth 认证域且存在 email 输入框）
                email_input = self.page.locator("input[type='email'], input[name='username'], input[name='email'], input#email-input").first
                if not (otp_handled or totp_handled or pwd_handled) and email_input.is_visible(timeout=1000):
                    _append_log(self.email, f"[登录] 检测到邮箱输入框，填入登录邮箱: {self.email}")
                    self.mouse.click_element(email_input)
                    try:
                        self.page.keyboard.press("Control+A")
                        self.page.keyboard.press("Backspace")
                    except Exception:
                        pass
                    self.page.keyboard.type(self.email, delay=35)
                    human_delay("click", minimum=0.4, maximum=0.8)
                    try:
                        self.page.keyboard.press("Enter")
                    except Exception:
                        pass
                    reauth_start_ts = time.time()
                    continue_btn = self.page.locator("button:has-text('Continue'), button:has-text('继续'), button[type='submit']").first
                    if continue_btn.is_visible(timeout=3000):
                        self.mouse.click_element(continue_btn)
                        time.sleep(3.0)
                        continue

                # 3. 密码输入阶段（URL 处于 /log-in/password 或检测到密码框）
                is_pwd_stage = (
                    "/log-in/password" in curr_url
                    or "/create-account/password" in curr_url
                    or "password" in curr_url.lower()
                    or (not pwd_handled and self.page.locator("input[type='password'], input[name*='password'], input#password").count() > 0)
                )
                if not pwd_handled and is_pwd_stage:
                    if password:
                        _append_log(self.email, "[登录] 处于密码阶段，填入登录密码...")
                        pwd_input = self.page.locator("input[type='password'], input[name*='password'], input#password").first
                        if pwd_input.is_visible(timeout=3000):
                            self.mouse.click_element(pwd_input)
                            self.page.keyboard.press("Control+A")
                            self.page.keyboard.press("Backspace")
                            self.page.keyboard.type(password, delay=30)
                            submit_btn = self.page.locator("button:has-text('Continue'), button:has-text('Log in'), button:has-text('继续'), button[type='submit']").first
                            if submit_btn.is_visible(timeout=3000):
                                self.mouse.click_element(submit_btn)
                                time.sleep(3.0)
                            pwd_handled = True
                            continue
                    else:
                        # 核心自愈：若无密码，优先尝试通过官方 Forgot Password 邮件设定永久密码
                        new_pwd = self._setup_or_reset_password()
                        if new_pwd:
                            password = new_pwd
                            pwd_handled = False
                            time.sleep(2.0)
                            continue

                        # 若设置密码未触发或失败，平滑走验证码/OTP切换
                        clicked_otp_switch = False
                        try:
                            clicked_otp_switch = self.page.evaluate("""() => {
                                const norm = s => String(s || '').replace(/\\s+/g, '').toLowerCase();
                                const candidates = Array.from(document.querySelectorAll('button, a, input[type="submit"], [role="button"]'));
                                for (const el of candidates) {
                                    const name = String(el.getAttribute('name') || '').toLowerCase();
                                    const val = String(el.getAttribute('value') || '').toLowerCase();
                                    const text = norm(el.innerText || el.textContent || val);
                                    if ((name === 'intent' && val.includes('passwordless')) ||
                                        val.includes('passwordless_login_send_otp') ||
                                        text.includes('emailacode') || text.includes('emailmeacode') ||
                                        text.includes('一次性验证码') || text.includes('一次性驗證碼') ||
                                        text.includes('验证码') || text.includes('メールでコード') ||
                                        text.includes('ワンタイムコード') || text.includes('codeper e-mail') ||
                                        text.includes('e-mail')) {
                                        el.click();
                                        return true;
                                    }
                                }
                                return false;
                            }""")
                            if clicked_otp_switch:
                                _append_log(self.email, "[登录] 成功通过 JS 探测并点击切换至邮箱验证码登录...")
                        except Exception:
                            pass

                        if not clicked_otp_switch:
                            email_code_link = self.page.locator(
                                "a:has-text('Email a code'), button:has-text('Email a code'), "
                                "a:has-text('Email me a code'), button:has-text('Email me a code'), "
                                "a:has-text('Email a temporary code'), button:has-text('Email a temporary code'), "
                                "a:has-text('发送代码'), button:has-text('发送代码'), "
                                "a:has-text('发送验证码'), button:has-text('发送验证码'), "
                                "a:has-text('使用验证码登录'), button:has-text('使用验证码登录'), "
                                "a:has-text('コードをメールで送信'), button:has-text('コードをメールで送信'), "
                                "a:has-text('Code per E-Mail'), button:has-text('Code per E-Mail'), "
                                "button[value='email_otp'], a[href*='email_otp'], a[href*='otp']"
                            ).first
                            if email_code_link.is_visible(timeout=2000):
                                _append_log(self.email, "[登录] 无保存密码，切换为发送邮箱验证码登录...")
                                self.mouse.click_element(email_code_link)
                                clicked_otp_switch = True

                        if clicked_otp_switch:
                            reauth_start_ts = time.time()
                            time.sleep(3.0)
                            pwd_handled = True
                            continue
                        else:
                            pwd_handled = True

                # 4. TOTP 双重认证阶段（若账号具备 totp_secret，最高优先级执行，防止误入邮箱等待）
                is_totp_prompt = (
                    any(kw in curr_page_text.lower() for kw in [
                        "authenticator app", "身份验证器", "totp", "mfa-totp-challenge",
                        "enter the code", "enter the 6-digit code", "enter code", "verification code", "验证码"
                    ])
                    or "mfa-challenge" in curr_url.lower()
                    or "totp" in curr_url.lower()
                    or self.page.locator("input[name='code'], input[placeholder*='code'], input[autocomplete='one-time-code'], input[inputmode='numeric']").count() > 0
                )
                is_totp_needed = bool(totp_secret) and is_totp_prompt

                if not totp_handled and is_totp_needed and (time.time() - last_totp_attempt_ts >= 6.0) and totp_attempts < 6:
                    last_totp_attempt_ts = time.time()
                    totp_attempts += 1
                    import pyotp
                    code = pyotp.TOTP(totp_secret).now()
                    _append_log(self.email, f"[2FA] 检测到 TOTP 挑战 (第 {totp_attempts} 次尝试)，计算动态码: {code}")

                    # 1. 优先尝试直接向 MFA 接口发送 API 验证请求
                    factor_id = curr_url.split("/")[-1].split("?")[0]
                    api_ok = False
                    if factor_id and len(factor_id) > 10:
                        try:
                            mfa_api_res = self.page.evaluate("""async ({factorId, code}) => {
                                try {
                                    const ctrl = new AbortController();
                                    const timer = setTimeout(() => ctrl.abort(), 10000);
                                    const res = await fetch('/api/accounts/mfa/verify', {
                                        method: 'POST',
                                        headers: {'Content-Type': 'application/json'},
                                        body: JSON.stringify({id: factorId, type: 'totp', code: code}),
                                        signal: ctrl.signal
                                    });
                                    clearTimeout(timer);
                                    const text = await res.text();
                                    let data = {};
                                    try { data = JSON.parse(text); } catch(e){}
                                    return {ok: res.ok, status: res.status, data: data, raw: text};
                                } catch(e) { return {ok: false, error: String(e)}; }
                            }""", {"factorId": factor_id, "code": code})
                            _append_log(self.email, f"[2FA] API 验证 TOTP 结果: {mfa_api_res}")
                            if mfa_api_res and mfa_api_res.get("ok") and mfa_api_res.get("data", {}).get("continue_url"):
                                target_url = mfa_api_res["data"]["continue_url"]
                                if target_url.startswith("/"):
                                    target_url = urllib.parse.urljoin(curr_url, target_url)
                                self.page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
                                api_ok = True
                                totp_handled = True
                                time.sleep(3.0)
                                continue
                        except Exception as mfa_err:
                            logger.debug("[Warming] API 验证 TOTP 异常: %s", mfa_err)

                    # 2. 备选：通过 DOM 模拟键入并触发 React 合成事件
                    if not api_ok:
                        try:
                            self.page.evaluate("""(code) => {
                                const el = document.querySelector("input[name='code'], input[placeholder*='code'], input[autocomplete='one-time-code'], input[inputmode='numeric'], input[type='text']");
                                if (el) {
                                    const nativeInputValueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
                                    nativeInputValueSetter.call(el, code);
                                    el.dispatchEvent(new Event('input', { bubbles: true }));
                                    el.dispatchEvent(new Event('change', { bubbles: true }));
                                }
                            }""", code)
                        except Exception:
                            pass

                        otp_input = self.page.locator("input[name='code'], input[placeholder*='code'], input[autocomplete='one-time-code'], input[inputmode='numeric'], input[type='text']").first
                        if otp_input.is_visible(timeout=1500):
                            self.mouse.click_element(otp_input)
                            self.page.keyboard.press("Control+A")
                            self.page.keyboard.press("Backspace")
                            self.page.keyboard.type(code, delay=40)
                            time.sleep(0.5)

                        otp_submit = self.page.locator("button:has-text('Continue'), button:has-text('Verify'), button:has-text('继续'), button[type='submit']").first
                        if otp_submit.is_visible(timeout=2000):
                            self.mouse.click_element(otp_submit)
                        else:
                            self.page.keyboard.press("Enter")

                    totp_handled = True
                    time.sleep(3.5)
                    continue

                # 5. 邮箱 OTP 验证码阶段（仅当账号无 totp_secret，且页面明确为邮件验证码时）
                is_email_otp_page = (
                    "email-verification" in curr_url
                    or "email_otp" in curr_url
                    or "mfa-email-challenge" in curr_url
                    or any(kw in curr_page_text.lower() for kw in [
                        "check your email", "we sent a code to", "email a temporary code", "メールでコード"
                    ])
                )

                if not otp_handled and not totp_secret and is_email_otp_page:
                    _append_log(self.email, "[登录] 检测到邮箱验证码挑战，正在从收件箱等待提取 6 位 OTP...")
                    from core.email_provider import wait_for_otp
                    otp_code = wait_for_otp(
                        self.email,
                        after_ts=reauth_start_ts - 5.0,
                        max_wait=35,
                        email_source=email_source,
                    )
                    if not otp_code:
                        _append_log(self.email, "[登录] 未能从邮箱获取到验证码，跳过邮件验证码阶段")
                        otp_handled = True
                        continue

                    _append_log(self.email, f"[登录] 成功获取邮箱验证码: {otp_code}，正在填入...")

                    val_res = None
                    try:
                        val_res = self.page.evaluate("""async (code) => {
                            try {
                                const ctrl = new AbortController();
                                const timer = setTimeout(() => ctrl.abort(), 10000);
                                const res = await fetch('/api/accounts/email-otp/validate', {
                                    method: 'POST',
                                    headers: {'Content-Type': 'application/json'},
                                    body: JSON.stringify({code: code}),
                                    signal: ctrl.signal
                                });
                                clearTimeout(timer);
                                const text = await res.text();
                                let data = {};
                                try { data = JSON.parse(text); } catch(e){}
                                if (!res.ok) return {ok: false, status: res.status, raw: text, error: (data && data.error) ? data.error : text};
                                return {ok: true, continue_url: data.continue_url};
                            } catch (e) {
                                return {ok: false, error: String(e)};
                            }
                        }""", otp_code)
                    except Exception as eval_err:
                        logger.debug("[Warming] evaluate validate OTP 异常: %s", eval_err)

                    if isinstance(val_res, dict):
                        raw_err = str(val_res.get("raw") or val_res.get("error") or "")
                        if "deactivated" in raw_err.lower() or "account_deactivated" in raw_err.lower():
                            _append_log(self.email, "[登录] 官方验证接口明确返回账号已封禁/停用 (account_deactivated)")
                            raise RuntimeError("账号已被封禁或停用 (deactivated)")

                    if isinstance(val_res, dict) and val_res.get("ok") and val_res.get("continue_url"):
                        target_url = val_res["continue_url"]
                        if target_url.startswith("/"):
                            target_url = urllib.parse.urljoin(curr_url, target_url)
                        self.page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
                    else:
                        try:
                            from core.auth_flow.otp_step import _type_otp
                            _type_otp(self.driver, otp_code, timeout=8)
                        except Exception as type_err:
                            logger.debug("[Warming] _type_otp fallback 异常: %s", type_err)
                            single_input = self.page.locator("input[name='code'], input[autocomplete='one-time-code'], input[inputmode='numeric']").first
                            if single_input.is_visible(timeout=2000):
                                self.mouse.click_element(single_input)
                                self.page.keyboard.type(otp_code, delay=50)
                        otp_submit = self.page.locator("button:has-text('Continue'), button:has-text('Verify'), button:has-text('继续'), button[type='submit']").first
                        if otp_submit.is_visible(timeout=2000):
                            self.mouse.click_element(otp_submit)

                    otp_handled = True
                    time.sleep(3.0)
                    continue

                # 6. 如果认证后在 auth.openai.com 停滞，尝试点击 Continue 授权
                if (otp_handled or totp_handled or pwd_handled) and "auth.openai.com" in curr_url and not is_email_otp_page:
                    cont_btn = self.page.locator("button:has-text('Continue'), button:has-text('继续'), button:has-text('Authorize'), button:has-text('授权')").first
                    if cont_btn.is_visible(timeout=1000):
                        _append_log(self.email, "[登录] 发现残留授权/继续按钮，点击放行...")
                        self.mouse.click_element(cont_btn)
                        time.sleep(2.0)

                # 阶段状态定期对账日志（每 5 秒打印一次）
                now = time.time()
                if now - last_stage_log_time > 5.0:
                    last_stage_log_time = now
                    _append_log(self.email, f"[登录流转中] 已耗时 {int(now - loop_start)}s | URL={curr_url[:80]} | 状态: OTP已处理={otp_handled}, TOTP已处理={totp_handled}")

                time.sleep(1.5)

            return False
        except Exception as exc:
            _append_log(self.email, f"[登录] 自动重登异常: {exc}")
            if "deactivated" in str(exc).lower():
                raise
            return False

    def _wait_for_stream_response(self, timeout_seconds: int = 60) -> bool:
        """等待 AI 流式输出生成完毕，伴随自然的视线滚动阅读。"""
        start_wait = time.time()
        while time.time() - start_wait < timeout_seconds:
            time.sleep(random.uniform(2.5, 4.0))
            # 模拟视线轻微上下滚动阅读
            try:
                self.page.mouse.wheel(delta_x=0, delta_y=random.randint(50, 140))
            except Exception:
                pass
            stop_btn = self.page.locator("button[data-testid='stop-button'], button[aria-label='Stop streaming']").first
            send_btn = self.page.locator("button[data-testid='send-button'], button[aria-label='Send prompt']").first
            if not stop_btn.is_visible(timeout=400) and send_btn.is_visible(timeout=400):
                return True
        return False

    def _simulate_reading_scroll(self) -> None:
        """50% 概率：在 AI 生成完成后做 2~4 次上下微调滚屏（模拟视线扫读与阅读理解）。"""
        if random.random() < float(getattr(_cfg, "WARMING_READ_SCROLL_PROBABILITY", 0.50)):
            num_scrolls = random.randint(2, 4)
            _append_log(self.email, f"[衍生行为] 触发阅读理解：上下微调滚屏 {num_scrolls} 次 (50% 概率网格)")
            for _ in range(num_scrolls):
                delta = random.choice([random.randint(60, 160), random.randint(-120, -40)])
                try:
                    self.page.mouse.wheel(delta_x=0, delta_y=delta)
                except Exception:
                    pass
                time.sleep(random.uniform(0.8, 1.8))

    def _simulate_past_chat_peek(self) -> bool:
        """15% 概率：点击侧边栏过去的某个旧会话，滚屏停留 5~10 秒假装翻阅。"""
        if random.random() < float(getattr(_cfg, "WARMING_PAST_CHAT_PROBABILITY", 0.15)):
            try:
                history_links = self.page.locator("nav a[href*='/c/']").all()
                if history_links and len(history_links) > 0:
                    peek_link = random.choice(history_links[:5])
                    _append_log(self.email, "[衍生行为] 触发翻阅历史会话：点击侧边栏旧对话并停留 5~10 秒假装翻阅 (15% 概率网格)...")
                    self.mouse.click_element(peek_link)
                    time.sleep(random.uniform(2.0, 3.5))
                    for _ in range(random.randint(2, 3)):
                        self.page.mouse.wheel(delta_x=0, delta_y=random.randint(100, 220))
                        time.sleep(random.uniform(1.2, 2.0))
                    linger_seconds = random.uniform(5.0, 9.0)
                    time.sleep(linger_seconds)
                    # 假装翻阅完成，返回当前工作流或点击新对话
                    new_chat_btn = self.page.locator("a[href='/'], button:has-text('New chat'), a:has-text('New chat')").first
                    if new_chat_btn.is_visible(timeout=2000):
                        self.mouse.click_element(new_chat_btn)
                    else:
                        self.page.go_back()
                    time.sleep(2.5)
                    _append_log(self.email, f"[衍生行为] 历史会话翻阅停留完成 ({linger_seconds:.1f}s)，切回工作流")
                    return True
            except Exception as exc:
                logger.debug("[Warming] 旧会话翻阅异常: %s", exc)
        return False

    def _action_natural_chat(self) -> dict:
        """行为1：发送 1~3 轮连贯对话树，模拟人类多轮深入交互与衍生行为概率网格。"""
        lang, category, prompt, followups, closings = load_random_prompt_tree(country=self.country)
        _append_log(self.email, f"[动作] 发起拟人对话 (语言: {lang}, 画像: {category}): {prompt}")

        # 查找输入框
        input_locator = self.page.locator("#prompt-textarea, div[contenteditable='true'], textarea[data-id='root']").first
        if not input_locator.is_visible(timeout=8000):
            raise RuntimeError("未在页面中找到 ChatGPT 提示词输入框")

        # 1. 模拟鼠标平滑滑向输入框并点击聚焦
        self.mouse.click_element(input_locator)
        human_delay("click", minimum=0.3, maximum=0.7)

        # 2. 拟人键入第一轮提问
        wpm = int(getattr(_cfg, "WARMING_TYPE_SPEED_WPM", 180) or 180)
        SemanticChunkTyper.type_text(self.page, prompt, wpm=wpm)
        human_delay("click", minimum=0.4, maximum=0.9)

        # 3. 发送第一轮
        if random.random() < 0.8:
            self.page.keyboard.press("Enter")
        else:
            send_btn = self.page.locator("button[data-testid='send-button'], button[aria-label='Send prompt']").first
            if send_btn.is_visible(timeout=2000):
                self.mouse.click_element(send_btn)
            else:
                self.page.keyboard.press("Enter")

        _append_log(self.email, "[动作] Turn 1 提问已发送，等待 AI 回复输出...")
        self._wait_for_stream_response(timeout_seconds=60)
        _append_log(self.email, "[动作] Turn 1 回答已生成完毕")
        self._simulate_reading_scroll()

        # 15% 概率触发侧边栏翻阅旧会话
        self._simulate_past_chat_peek()

        turns_executed = 1
        second_prompt = None
        third_prompt = None

        # 4. 多轮追问演进：按配置的 dialogue_turns 判断是否发起第 2 轮连贯深入追问
        if followups and self.dialogue_turns >= 2 and random.random() < 0.85:
            second_prompt = random.choice(followups)
            reading_pause = random.uniform(3.5, 6.5)
            _append_log(self.email, f"[动作] 模拟人类阅读理解 ({reading_pause:.1f}s)，发起 Turn 2 连贯追问: {second_prompt}")
            time.sleep(reading_pause)

            input_locator = self.page.locator("#prompt-textarea, div[contenteditable='true']").first
            if input_locator.is_visible(timeout=4000):
                self.mouse.click_element(input_locator)
                human_delay("click", minimum=0.2, maximum=0.5)
                SemanticChunkTyper.type_text(self.page, second_prompt, wpm=int(wpm * 1.05))
                human_delay("click", minimum=0.3, maximum=0.7)
                self.page.keyboard.press("Enter")

                _append_log(self.email, "[动作] Turn 2 追问已发送，等待 AI 回复输出...")
                self._wait_for_stream_response(timeout_seconds=60)
                _append_log(self.email, "[动作] Turn 2 回答已生成完毕")
                self._simulate_reading_scroll()
                turns_executed = 2

                # 5. 第 3 轮：若配置允许 3 轮以上，45% 概率发送简短感谢收尾
                if closings and self.dialogue_turns >= 3 and random.random() < 0.45:
                    third_prompt = random.choice(closings)
                    thank_pause = random.uniform(2.5, 4.5)
                    _append_log(self.email, f"[动作] 模拟人类满意收尾 ({thank_pause:.1f}s)，发送 Turn 3 简短致谢: {third_prompt}")
                    time.sleep(thank_pause)

                    input_locator = self.page.locator("#prompt-textarea, div[contenteditable='true']").first
                    if input_locator.is_visible(timeout=4000):
                        self.mouse.click_element(input_locator)
                        human_delay("click", minimum=0.2, maximum=0.4)
                        SemanticChunkTyper.type_text(self.page, third_prompt, wpm=int(wpm * 1.1))
                        self.page.keyboard.press("Enter")
                        self._wait_for_stream_response(timeout_seconds=30)
                        self._simulate_reading_scroll()
                        turns_executed = 3

        # 6. 衍生互动行为概率网格：复制 (35%) 与 点赞 (25%)
        if random.random() < float(getattr(_cfg, "WARMING_COPY_PROBABILITY", 0.35)):
            try:
                copy_btn = self.page.locator("button[aria-label='Copy'], button[data-testid='copy-turn-action-button']").last
                if copy_btn.is_visible(timeout=1500):
                    time.sleep(random.uniform(1.0, 2.5))
                    self.mouse.click_element(copy_btn)
                    _append_log(self.email, "[动作] 模拟点击了回复内容【复制 Copy】按钮 (35% 概率网格)")
            except Exception:
                pass

        if random.random() < float(getattr(_cfg, "WARMING_UPVOTE_PROBABILITY", 0.25)):
            try:
                upvote_btn = self.page.locator("button[aria-label='Good response'], button[aria-label='Thumbs up']").last
                if upvote_btn.is_visible(timeout=1500):
                    time.sleep(random.uniform(0.8, 2.0))
                    self.mouse.click_element(upvote_btn)
                    _append_log(self.email, "[动作] 模拟点击了回复内容【点赞 Thumbs Up】按钮 (25% 概率网格)")
            except Exception:
                pass

        return {
            "action": "natural_chat",
            "prompt": prompt,
            "category": category,
            "language": lang,
            "followup": second_prompt,
            "closing": third_prompt,
            "turns": turns_executed,
        }


    def _action_browse_history(self) -> dict:
        """行为2：翻阅历史会话，模拟人类回看旧对话。"""
        _append_log(self.email, "[动作] 执行历史会话翻阅...")
        history_links = self.page.locator("nav a[href*='/c/']").all()
        if history_links and len(history_links) > 0:
            target_link = random.choice(history_links[:5])
            self.mouse.click_element(target_link)
            time.sleep(random.uniform(3.0, 5.0))
            for _ in range(random.randint(2, 4)):
                self.page.mouse.wheel(delta_x=0, delta_y=random.randint(150, 280))
                time.sleep(random.uniform(1.5, 3.0))
            for _ in range(random.randint(1, 2)):
                self.page.mouse.wheel(delta_x=0, delta_y=random.randint(-200, -100))
                time.sleep(random.uniform(1.2, 2.0))
            _append_log(self.email, "[动作] 历史会话翻阅完成")
            return {"action": "browse_history", "detail": "viewed_past_chat"}
        else:
            _append_log(self.email, "[动作] 当前账号无历史会话，转为主页探索")
            return self._action_browse_home()

    def _action_browse_home(self) -> dict:
        """行为3：主页漫游，鼠标随机晃动、滚动与探索。"""
        _append_log(self.email, "[动作] 执行主页与探索漫游...")
        for _ in range(random.randint(3, 5)):
            rx = random.randint(150, 800)
            ry = random.randint(100, 600)
            self.mouse.move_to(rx, ry)
            time.sleep(random.uniform(0.5, 1.2))
            self.page.mouse.wheel(delta_x=0, delta_y=random.randint(-80, 160))
            time.sleep(random.uniform(0.8, 2.0))

        gpts_link = self.page.locator("a[href*='/gpts'], a:has-text('Explore GPTs')").first
        if gpts_link.is_visible(timeout=1500):
            try:
                box = gpts_link.bounding_box()
                if box:
                    self.mouse.move_to(box["x"] + 20, box["y"] + 10)
                    time.sleep(1.2)
            except Exception:
                pass
        return {"action": "browse_home", "detail": "home_exploration"}

    def run(self) -> dict:
        """执行一次完整的养号流程。"""
        start_time = time.time()
        _append_log(self.email, "=" * 55, clear=False)
        _append_log(self.email, f"[养号] 开始为账号执行拟人养号: {self.email} (ID: {self.acc_id})")

        result = {
            "ok": False,
            "status": "failed",
            "email": self.email,
            "account_id": self.acc_id,
            "started_at": datetime.now().isoformat(),
            "action": None,
            "prompt": None,
            "dwell_seconds": 0,
            "storage_state": None,
            "error": None,
        }

        try:
            # 1. 启动指纹浏览器
            self._init_browser()

            # 2. 打开 ChatGPT 首页
            _append_log(self.email, "[导航] 访问 https://chatgpt.com ...")
            for nav_idx in range(2):
                try:
                    self.page.goto("https://chatgpt.com", wait_until="domcontentloaded", timeout=25000)
                    break
                except Exception as nav_err:
                    _append_log(self.email, f"[导航] 页面加载提示 (第 {nav_idx + 1} 次): {nav_err}")
                    try:
                        self.page.keyboard.press("Escape")
                    except Exception:
                        pass
                    time.sleep(2.0)
            time.sleep(random.uniform(2.0, 3.5))
            self._guard_page()

            # 3. 检查登录态与封号情况
            is_logged_in, reason = self._check_session_alive()
            if not is_logged_in:
                if reason == "deactivated":
                    raise RuntimeError("账号已被 OpenAI 停用或封禁 (deactivated)")
                if not self._try_relogin():
                    raise RuntimeError("账号未登录且自动重登失败，可能需手动核验或密码不符")

            # 深度省流与自愈分流：若未开启拟人问答交互，登录态验证/补登成功后直接完成自愈并导出凭据
            if not self.enable_dialogue:
                _append_log(self.email, "[自愈] 极速自愈恢复模式：跳过后续拟人问答与漫游，即刻导出凭据以最大化节省流量")
                try:
                    context = getattr(self.driver, "context", None)
                    if context:
                        new_state = context.storage_state()
                        result["storage_state"] = new_state
                        _append_log(self.email, "[状态] 已捕获最新 StorageState 免登凭证")
                except Exception as exc:
                    logger.debug("[Warming] 导出 StorageState 失败: %s", exc)

                total_elapsed = time.time() - start_time
                result["ok"] = True
                result["status"] = "success"
                result["action"] = "fast_recovery"
                result["prompt"] = ""
                result["dwell_seconds"] = total_elapsed
                if getattr(self, "latest_access_token", None):
                    result["access_token"] = self.latest_access_token
                _append_log(self.email, f"[完成] 极速自愈恢复成功（超低流量模式）！总耗时: {total_elapsed:.1f}s")
                return result

            # 4. 根据权重抽取本次养号动作
            w_chat = int(getattr(_cfg, "WARMING_WEIGHT_CHAT", 65))
            w_hist = int(getattr(_cfg, "WARMING_WEIGHT_HISTORY", 20))
            w_brow = int(getattr(_cfg, "WARMING_WEIGHT_BROWSE", 15))
            choices = ["chat", "history", "browse"]
            weights = [w_chat, w_hist, w_brow]
            selected_mode = random.choices(choices, weights=weights, k=1)[0]

            # 尝试关闭常见遮挡弹窗
            for dismiss_sel in (
                "button:has-text('Dismiss')", "button:has-text('Okay, let\\'s go')", "button:has-text('Stay logged out')",
                "button:has-text('Close')", "button:has-text('Got it')", "button:has-text('关闭')", "button:has-text('我知道了')"
            ):
                try:
                    d_btn = self.page.locator(dismiss_sel).first
                    if d_btn.is_visible(timeout=500):
                        d_btn.click()
                        time.sleep(0.5)
                except Exception:
                    pass

            action_data = {}
            try:
                if selected_mode == "chat":
                    action_data = self._action_natural_chat()
                elif selected_mode == "history":
                    action_data = self._action_browse_history()
                else:
                    action_data = self._action_browse_home()
            except Exception as act_err:
                _append_log(self.email, f"[动作] {selected_mode} 动作偶发异常 ({act_err})，自动降级为主页漫游探索...")
                try:
                    action_data = self._action_browse_home()
                except Exception as fallback_err:
                    action_data = {"action": "dwell_fallback", "dwell": 5.0}

            # 5. 补充自然停留时长 (Dwell Time)
            target_dwell = random.uniform(
                float(getattr(_cfg, "WARMING_MIN_DWELL_SECONDS", 35.0)),
                float(getattr(_cfg, "WARMING_MAX_DWELL_SECONDS", 90.0)),
            )
            elapsed = time.time() - start_time
            remain_dwell = max(4.0, target_dwell - elapsed)
            _append_log(self.email, f"[停留] 模拟自然停留在页面阅读思考: {remain_dwell:.1f}s")
            time.sleep(remain_dwell)

            # 6. 导出并保存最新 StorageState (Cookies+LocalStorage)
            try:
                context = getattr(self.driver, "context", None)
                if context:
                    new_state = context.storage_state()
                    result["storage_state"] = new_state
                    _append_log(self.email, "[状态] 已捕获最新 StorageState 免登凭证")
            except Exception as exc:
                logger.debug("[Warming] 导出 StorageState 失败: %s", exc)

            total_elapsed = time.time() - start_time
            result["ok"] = True
            result["status"] = "success"
            result["action"] = action_data.get("action")
            result["prompt"] = action_data.get("prompt")
            result["dwell_seconds"] = total_elapsed
            if getattr(self, "latest_access_token", None):
                result["access_token"] = self.latest_access_token
            _append_log(self.email, f"[完成] 养号成功！总耗时: {total_elapsed:.1f}s")

        except Exception as exc:
            err_msg = str(exc)
            logger.error("[Warming] 养号执行失败: %s - %s", self.email, err_msg)
            _append_log(self.email, f"[失败] 养号过程异常: {err_msg}")
            result["ok"] = False
            result["status"] = "failed"
            result["error"] = err_msg

            # 若检测到封号，深度协同写回数据库，标记 dead / deactivated 并阻断后续调度
            if "deactivated" in err_msg.lower() or "封禁" in err_msg or "停用" in err_msg:
                _append_log(self.email, "[封号] 正在联动将账号存活状态标记为 deactivated 停用...")
                try:
                    db.update_account_liveness(self.acc_id, {"status": "deactivated", "error": err_msg})
                except Exception as db_err:
                    logger.warning("[Warming] 协同写回封号状态异常: %s", db_err)

        finally:
            # 停止省流拦截器并记录统计
            if getattr(self, "data_saver", None):
                try:
                    self.data_saver.stop()
                    stats = self.data_saver.stats()
                    _append_log(
                        self.email,
                        f"[省流] 拦截统计: 成功拦截 {stats.get('blocked_count', 0)} 个可选静态与遥测请求 "
                        f"(类型分布: {dict(stats.get('blocked_by_type', {}))})"
                    )
                except Exception:
                    pass
            # 优雅退出浏览器
            if self.driver:
                try:
                    self.driver.quit()
                except Exception:
                    pass
            # 写回数据库
            db.update_account_warming_result(self.acc_id, result)

        return result


def warm_single_account(
    account_id: int,
    *,
    trigger: str = "manual",
    headless: bool | None = None,
    enable_dialogue: bool | None = None,
    dialogue_turns: int | None = None,
) -> dict:
    """对指定 ID 的账号执行单次自愈恢复或拟人养号。"""
    acc = db.get_account(account_id)
    if not acc:
        return {"ok": False, "error": f"账号不存在 (ID: {account_id})"}

    # 若未被调度器原子占用（如外部或单独调用），尝试占用；若已处于 queued/running 队列则直接推进执行，消除死锁
    status = str(acc.get("warming_status") or "")
    if status not in {"queued", "running"}:
        if not db.claim_account_warming(account_id, trigger=trigger):
            return {"ok": False, "error": "账号已在养号队列或正在执行中"}

    db.mark_account_warming_running(account_id)
    service = AccountWarmingService(
        acc,
        headless=headless,
        enable_dialogue=enable_dialogue,
        dialogue_turns=dialogue_turns,
    )
    return service.run()

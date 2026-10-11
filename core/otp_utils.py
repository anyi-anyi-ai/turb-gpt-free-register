# -*- coding: utf-8 -*-
"""
OTP 检测与抽取通用工具，被 outlook_client（Outlook 邮箱）使用。

要求：
    - 多语言关键字识别（英 / 中 / 日 / 韩）
    - 字段名容错（不同邮件 API 用不同的字段命名约定）
    - 上下文优先：在多个 6 位数中，选择离"验证码"等关键字最近的那个
"""
import re

_OPENAI_SENDER_HINT = "openai"
_GROK_SENDER_HINTS = ("x.ai", "xai", "grok")

# 多语言关键字（用于判断是否是 OpenAI / Grok 验证码邮件）
_OPENAI_KEYWORDS = (
    "chatgpt", "openai", "grok", "x.ai", "xai", "spacexai", "spacex",
    "suno", "clerk", "runninghub", "running hub",
    # UNED / 西班牙文
    "uned", "código", "codigo", "verificación", "verificacion", "validar",
    # 英文
    "verification code", "code is", "your code", "verify your email", "validate your email",
    "security code", "one time security code", "one time code", "validate",
    # 中文
    "代码", "验证码", "确认码", "安全码",
    # 日文
    "認証コード", "検証コード", "確認コード", "一時検証", "認証",
    # 韩文
    "인증 코드", "확인 코드",
)

# OTP 上下文关键字（用于在多个 6 位数中挑出真正的验证码）
_OTP_CONTEXT_KEYWORDS = (
    "code", "verify", "verification", "security", "validate",
    "código", "codigo", "uned",
    "代码", "验证", "确认", "安全",
    "コード", "認証", "検証", "確認",
    "코드", "인증",
)

_OTP_REGEX = re.compile(r"\b(\d{6})\b")
_OTP_HYPHEN_REGEX = re.compile(r"\b(\d{3})-(\d{3})\b")


def _find_all_otps(text: str) -> list[str]:
    """抽取 6 位数字或带短横线的 3-3 格式验证码并归一化为 6 位数字。"""
    if not text:
        return []
    res = []
    # 优先提取带短横线的 3-3 格式 (例如 SpaceXAI / Grok: 572-757)
    for m in _OTP_HYPHEN_REGEX.finditer(text):
        res.append(f"{m.group(1)}{m.group(2)}")
    # 提取纯 6 位连续数字
    for m in _OTP_REGEX.finditer(text):
        res.append(m.group(1))
    seen = set()
    out = []
    for c in res:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _get_field(item: dict, *names: str) -> str:
    """
    从邮件 dict 中按顺序尝试多个可能的字段名，返回第一个非空字符串。
    用于兼容不同邮件 API 的字段命名约定（例如 sendEmail / from / fromEmail / from.address）。
    """
    for name in names:
        if "." in name:
            # 支持 "from.emailAddress.address" 这种点路径
            value = item
            for part in name.split("."):
                if not isinstance(value, dict):
                    value = None
                    break
                value = value.get(part)
            if isinstance(value, str) and value:
                return value
        else:
            value = item.get(name)
            if isinstance(value, str) and value:
                return value
    return ""


def looks_like_openai_email(item: dict) -> bool:
    """
    判断邮件是否来自 OpenAI / ChatGPT 或 xAI / Grok。多语言、多字段名兼容。

    字段名容错（不同 API 返回风格不一）：
        发件人:  sendEmail / from / fromEmail / from.emailAddress.address
        发件人名:sendName / fromName / from.emailAddress.name
        纯文本:  text / bodyPreview / bodyText
        HTML:    content / body / html / body.content / bodyHtml
    """
    sender = _get_field(item, "sendEmail", "from", "fromEmail", "from.emailAddress.address").lower()
    sender_name = _get_field(item, "sendName", "fromName", "from.emailAddress.name").lower()
    subject = _get_field(item, "subject").lower()
    text = _get_field(item, "text", "bodyPreview", "bodyText").lower()
    content = _get_field(item, "content", "body", "html", "body.content", "bodyHtml").lower()

    if _OPENAI_SENDER_HINT in sender or _OPENAI_SENDER_HINT in sender_name:
        return True
    if any(h in sender or h in sender_name for h in _GROK_SENDER_HINTS):
        return True
    if any(h in sender or h in sender_name for h in ("muse", "meta", "facebook", "instagram", "suno", "clerk")):
        return True

    return any(k in s for s in (subject, text, content) for k in _OPENAI_KEYWORDS)


def extract_otp(item: dict) -> str | None:
    """
    从邮件中抽出 6 位 OTP。

    抽取顺序：
        1. subject（OpenAI / Grok 部分邮件直接把 6 位数放在主题里）
        2. 纯文本字段（text / bodyPreview / bodyText）
        3. HTML 字段（content / html / body / body.content / bodyHtml，去标签后）

    若 body 中含多个 6 位数，优先选择离 "验证码 / code / 認証 / validate" 等关键字最近的那个。
    """
    # 1. 主题里如果直接有 6 位数，最可信
    subject = _get_field(item, "subject")
    if subject:
        codes_in_subject = _find_all_otps(subject)
        if len(codes_in_subject) == 1:
            return codes_in_subject[0]

    # 2. body 字段
    candidates = [
        ("text", _get_field(item, "text", "bodyPreview", "bodyText")),
        ("html", _get_field(item, "content", "html", "body", "body.content", "bodyHtml")),
    ]

    for kind, body in candidates:
        if not body:
            continue
        # 无论 text 还是 html，都先去 HTML 标签和 style 属性
        body_clean = re.sub(r"<style[^>]*>.*?</style>", " ", body, flags=re.DOTALL | re.IGNORECASE)
        body_clean = re.sub(r"<[^>]+>", " ", body_clean)
        all_codes = _find_all_otps(body_clean)
        if not all_codes:
            continue
        body_lower = body_clean.lower()
        # 优先选离上下文关键字最近的 6 位数（包括带短横线的位置）
        for code in all_codes:
            hyphen_code = f"{code[:3]}-{code[3:]}"
            idx = body_lower.find(code)
            if idx < 0:
                idx = body_lower.find(hyphen_code)
            if idx < 0:
                continue
            window = body_lower[max(0, idx - 80): idx + len(code) + 80]
            if any(k.lower() in window for k in _OTP_CONTEXT_KEYWORDS):
                return code
        return all_codes[0]
    return None


def mask_otp(otp: str | None) -> str:
    """对 OTP 验证码进行脱敏（例如 '123456' -> '1****6'），防止在日志中泄露敏感一次性密码。"""
    if not otp:
        return ""
    s = str(otp).strip()
    if len(s) <= 2:
        return "***"
    return f"{s[0]}{'*' * (len(s) - 2)}{s[-1]}"


def mask_secret(secret: str | None) -> str:
    """脱敏密码或 Token，避免在日志中打印明文凭据。"""
    if not secret:
        return "***"
    s = str(secret)
    if len(s) <= 4:
        return "***"
    return f"{s[:2]}***{s[-2:]}"

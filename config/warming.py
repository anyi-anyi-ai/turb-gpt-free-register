# -*- coding: utf-8 -*-
"""ChatGPT 账号拟人养号与后期维护配置。"""
from config.env_loader import apply_env_overrides

# 养号执行驱动：'cloak'（推荐，基于 Playwright + CloakBrowser）或 'roxy'（基于 RoxyBrowser）
WARMING_DRIVER: str = "cloak"

# 养号运行模式：False=显示浏览器窗口（便于观察），True=无头后台运行
WARMING_HEADLESS: bool = False

# 自动定时养号调度器开关
WARMING_SCHEDULER_ENABLED: bool = False

# 账号推荐养号周期间隔（天），默认距上次养号超过 3 天可再次唤醒
WARMING_INTERVAL_DAYS: int = 3

# 养号任务并发执行数（建议 1~2，防止浏览器占用过多内存与 CPU）
WARMING_CONCURRENCY: int = 1

# 允许自动静默养号的时间窗口（按本地时间），避免在深夜异常活跃
WARMING_ACTIVE_HOURS: str = "08:00-23:00"

# 混合行为概率权重（总和建议为 100）
WARMING_WEIGHT_CHAT: int = 65      # 自然日常问答 + 流式阅读 + 随机复制点赞
WARMING_WEIGHT_HISTORY: int = 20   # 翻阅历史旧会话 + 视线滚动
WARMING_WEIGHT_BROWSE: int = 15    # 首页停留 + 探索 GPTs 页面浏览

# 单次养号在页面停留互动的时长范围（秒）
WARMING_MIN_DWELL_SECONDS: float = 35.0
WARMING_MAX_DWELL_SECONDS: float = 90.0

# 模拟打字速度（每分钟字数，加入正态随机波动）
WARMING_TYPE_SPEED_WPM: int = 180

# 对话完成后点赞 (Thumbs Up) 的概率
WARMING_UPVOTE_PROBABILITY: float = 0.25

# 对话完成后复制回复 (Copy) 的概率
WARMING_COPY_PROBABILITY: float = 0.40

# 本地日常语料库相对路径
WARMING_CORPUS_FILE: str = "config/prompts_corpus.json"

# 是否优先复用账号注册时记录的原代理
WARMING_USE_STICKY_PROXY: bool = True

# ---- .env overrides for WebUI editable fields ----
apply_env_overrides(globals(), {
    'WARMING_DRIVER': 'str',
    'WARMING_HEADLESS': 'bool',
    'WARMING_SCHEDULER_ENABLED': 'bool',
    'WARMING_INTERVAL_DAYS': 'int',
    'WARMING_CONCURRENCY': 'int',
    'WARMING_ACTIVE_HOURS': 'str',
    'WARMING_WEIGHT_CHAT': 'int',
    'WARMING_WEIGHT_HISTORY': 'int',
    'WARMING_WEIGHT_BROWSE': 'int',
    'WARMING_MIN_DWELL_SECONDS': 'float',
    'WARMING_MAX_DWELL_SECONDS': 'float',
    'WARMING_TYPE_SPEED_WPM': 'int',
    'WARMING_UPVOTE_PROBABILITY': 'float',
    'WARMING_COPY_PROBABILITY': 'float',
    'WARMING_CORPUS_FILE': 'str',
    'WARMING_USE_STICKY_PROXY': 'bool',
})

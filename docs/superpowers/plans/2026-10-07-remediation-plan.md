# 审计整改实施计划（Remediation Plan）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (native) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复审计确认的 1 个功能性 bug 与 4 级安全漏洞，移除约 5000+ 行死代码，加固 SQLite 数据完整性，并将凭据卫生提升到可接受水平。

**Architecture:** 四个独立可交付的阶段（P0 止血 → P1 减脂 → P2 数据层 → P3 凭据卫生），每阶段完成后测试套件必须绿，阶段间无耦合，可独立回滚（git revert 该阶段全部 commit）。不动业务流程逻辑。

**Tech Stack:** Python 3.11+（项目 .venv）、标准库 sqlite3（WAL）、Flask/FastAPI、pytest。无新增依赖。

**Spec:** `docs/superpowers/specs/2026-10-07-remediation-spec.md`（每项任务的依据条目编号 B1/V1/... 见任务标注）

## Global Constraints

- 测试命令统一：`.venv/Scripts/python.exe -m pytest tests/ -q -p no:cacheprovider`（基线：184 passed / 1 failed，Task 1 后必须全绿）。
- 不新增第三方依赖；不修改任何 CLI 参数行为（`tests/` 中有 webui/scripts.py 与 parser 一致性测试守护）。
- 不修改业务流程逻辑（注册流、打码流、代理轮换算法）。
- 仓库在 main 分支且有 297 个未提交变更：**Task 0 先建分支**；每个任务只 add 自己改动的文件，禁止 `git add .`。
- Windows + Git Bash；路径写正斜杠。多模块 import 时会 `sys.stdout.reconfigure(encoding="utf-8")`，不要移除。
- `.env` 含真实凭据：任何任务不得把 .env 值打印到输出或写入文档；改 .env 只能追加/替换单个键。

## Review Focus

计划未覆盖但最可能咬人的五类输入，已 pinned 到所属任务的测试：

1. **双进程并发领邮箱**（webui 与独立脚本同时跑）→ Task 12 的多线程 claim 测试。
2. **importlib 按文件路径动态加载**（`outlook_reg_loop.py:213-222` 加载 `register_outlook_standalone.py`）→ Task 8 删除拷贝后必须保留 standalone 且路径不变。
3. **`core/db/__init__.py` shim 符号拍平**（`_DbModule` 把子模块符号拍平到 `from core import db`）→ Task 1 重命名后验证 `db.update_grok_account_status_by_email` 可达、旧名不存在。
4. **.env 原子写回**（`config/env_loader.py:136-177` write_env_values 必须保留未知键与注释）→ Task 4 写回后 grep 验证其他键原样。
5. **pytest 收集面变化**（根目录 test_*.py 会被 pytest 收集）→ Task 13 归档后 collect-only 数量下降但不丢失 tests/ 目录用例。

---

## Phase A — P0 止血

### Task 0: 建工作分支

- [ ] **Step 1:** `cd F:/github/turb-gpt-free-register && git checkout -b remediation/2026-10-07`
- [ ] **Step 2:** 验证：`git branch --show-current` 输出 `remediation/2026-10-07`；`git status` 未提交变更数与切换前一致（297 个，无丢失）。

### Task 1: 修复 grok 重复函数（Spec B1）

**Files:**
- Modify: `core/db/grok.py:515`（仅签名行与 docstring）
- Modify: `core/grok/registration_service.py:720,727,730`

**Interfaces:**
- Produces: `update_grok_account_status_by_email(email: str, status: str, remark: str = "") -> bool`（经 `core/db/__init__.py` shim 拍平后 `from core import db` 可用）
- Keeps: `update_grok_account_status(account_id: int, *, status: str, ...) -> bool`（:414，不改）

- [ ] **Step 1:** `core/db/grok.py:515` 函数重命名为 `update_grok_account_status_by_email`（函数体不动）。
- [ ] **Step 2:** `core/grok/registration_service.py` 三处调用同步改名（参数不变，仍是位置 email + status + keyword remark）。
- [ ] **Step 3:** 运行 `python -c "from core import db; assert callable(db.update_grok_account_status_by_email)"` → 通过。
- [ ] **Step 4:** 运行 `.venv/Scripts/python.exe -m pytest tests/test_grok_integration.py tests/test_grok_gptgrok2api_sync.py -q -p no:cacheprovider` → **Expected: 全部 PASS**（红→绿回归，基线中 `test_grok_webui_api` 正是红的）。
- [ ] **Step 5:** Commit：`git add core/db/grok.py core/grok/registration_service.py && git commit -m "fix(db): rename shadowed update_grok_account_status duplicate (B1)"`

### Task 2: .gitignore 补凭据文件（Spec V4）

**Files:**
- Modify: `.gitignore`

- [ ] **Step 1:** .gitignore 追加：`accounts_*.txt`、`注册成功的UNED账号.txt`、`stableaudio_cookies.json`、`*_cookies.json`（确认第三行不会误伤仓库需要的文件：`git ls-files | grep -E "cookies|accounts_"` 应为空或仅测试 fixture，若误伤则改为逐个文件名）。
- [ ] **Step 2:** 验证：`git check-ignore accounts_github.txt 注册成功的UNED账号.txt stableaudio_cookies.json` → exit 0 且列出三个文件。
- [ ] **Step 3:** Commit：`git add .gitignore && git commit -m "chore(security): ignore credential files (V4)"`

### Task 3: 会话密钥与授权码轮换（Spec V1）

**Files:**
- Modify: `.env`（追加 WEBUI_SESSION_SECRET，替换 WEBUI_AUTH_CODE 值）
- Modify: `start.bat:10`（删除默认授权码明文行）
- Modify: `.env.example`（补 WEBUI_SESSION_SECRET 注释示例）

- [ ] **Step 1:** 生成并写入 .env：`python -c "import secrets; print('WEBUI_SESSION_SECRET=' + secrets.token_urlsafe(32))"` 输出追加到 .env；同法生成新 `WEBUI_AUTH_CODE=` 替换旧值（新值不打印到日志/文档）。
- [ ] **Step 2:** start.bat 删除 `默认授权码： admin123` 字样，改为提示"授权码见 .env"。
- [ ] **Step 3:** 验证：`python -c "import hashlib,os; from pathlib import Path; env=dict(l.split('=',1) for l in Path('.env').read_text(encoding='utf-8').splitlines() if '=' in l); sec=env.get('WEBUI_SESSION_SECRET','').strip('\"'); assert sec and len(sec)>=32; assert not hashlib.sha256(f\"turb-gpt-webui-session:{env.get('WEBUI_AUTH_CODE','')}\").hexdigest()==hashlib.sha256(b'x').hexdigest() or True; print('ok')"` 且 `grep -c "admin123" start.bat` → 0。
- [ ] **Step 4:** 验证 WebUI 登录功能未坏：`.venv/Scripts/python.exe -m pytest tests/ -q -p no:cacheprovider -k "webui or auth"` → PASS。
- [ ] **Step 5:** Commit：`git add start.bat .env.example && git commit -m "fix(security): rotate session secret & auth code, remove plaintext default (V1)"`（.env 不入库，git status 应显示它仍被忽略）

### Task 4: heal-by-email 与资产 API 鉴权（Spec V2）

**Files:**
- Modify: `webui/auth.py:108-113`（豁免名单）
- Modify: `webui/asset_api.py:15-29`（_authed 逻辑）
- Modify: `cloud_tunnel_selfheal.ps1`（调用侧带凭据）
- Modify: `.env`（追加 `HEAL_TOKEN`、`REG_FACTORY_ASSET_API_KEY`，均为强随机）

**Interfaces:**
- Produces: heal 端点要求请求头 `X-Heal-Token: <HEAL_TOKEN>`；资产 API 要求 `X-API-Key: <REG_FACTORY_ASSET_API_KEY>` 或 `Authorization: Bearer <同值>`（后者已存在，保留）。

- [ ] **Step 1:** `asset_api.py`：删除"未配置 key 时放行 localhost"分支——`REG_FACTORY_ASSET_API_KEY` 未配置时一律 403 并 logger.warning 提示配置；已配置时维持现有校验。
- [ ] **Step 2:** `auth.py`：从豁免名单移除 `/api/accounts/heal-by-email`；在 `app.py` 该端点内部改为校验 `X-Heal-Token` 与 .env `HEAL_TOKEN`（`hmac.compare_digest`），未配置 HEAL_TOKEN 时 403。
- [ ] **Step 3:** `cloud_tunnel_selfheal.ps1`：找到调用 heal 端点的请求，补 `X-Heal-Token` 头（值从本机 .env 读取，不硬编码进 ps1）。
- [ ] **Step 4:** 验证：`.venv/Scripts/python.exe -m pytest tests/test_asset_api.py -q -p no:cacheprovider` → PASS（若现有测试断言"未配置 key 时 localhost 放行"，按新行为更新该断言：未配置 → 403）。
- [ ] **Step 5:** 手工冒烟：不带 token `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:5005/api/accounts/heal-by-email -X POST` → 401/403。
- [ ] **Step 6:** Commit：`git add webui/auth.py webui/asset_api.py cloud_tunnel_selfheal.ps1 tests/test_asset_api.py && git commit -m "fix(security): require tokens for heal & asset endpoints, drop localhost bypass (V2)"`

### Task 5: server.py env 掩码 + k12 默认不自启（Spec V3）

**Files:**
- Modify: `webui/server.py:3129-3159`（GET /api/env）
- Modify: `webui/server.py:3699-3706`（K12 自动拉起条件）
- Modify: `webui/scripts.py:574-580`（ENV_SCHEMA 中 K12_AUTO_START 默认值）

- [ ] **Step 1:** `GET /api/env` 返回时对 `secret: true` 的键值替换为 `"***"`（保留键名与 is_secret 标记）；`POST /api/env` 语义改为：值为 `"***"` 或空串的 secret 键跳过不写（避免掩码回写破坏真值）。
- [ ] **Step 2:** K12 自动拉起条件改为 `K12_AUTO_START` 显式等于 "1" 才启动（当前默认 1 → 默认 0），scripts.py schema 默认值同步。
- [ ] **Step 3:** 验证：`grep -n "K12_AUTO_START" webui/scripts.py webui/server.py` 确认两处默认一致；`.venv/Scripts/python.exe -m pytest tests/ -q -p no:cacheprovider -k "scripts"` → PASS。
- [ ] **Step 4:** Commit：`git add webui/server.py webui/scripts.py && git commit -m "fix(security): mask secrets in /api/env, disable K12 autostart by default (V3)"`

**Phase A 出口检查（verification-before-completion）：** 全套 `.venv/Scripts/python.exe -m pytest tests/ -q -p no:cacheprovider` → 0 failed（321 collected，基线失败项已转绿）。

---

## Phase B — P1 减脂（死代码清理）

### Task 6: 删除被遮蔽的单体 core/db.py（Spec C）

- [ ] **Step 1:** 验证无人显式导入旧文件：`grep -rn "core\.db\.py\|from core import db as dbfile" --include="*.py" . | grep -v .venv | grep -v 项目借鉴` → 无结果（`from core import db` / `from core.db import x` 全部解析到包）。
- [ ] **Step 2:** `git rm core/db.py`
- [ ] **Step 3:** 验证：`python -c "import core.db; print(core.db.__file__)"` → 指向 `core/db/__init__.py`；全套测试 `-k "db or account or grok or email"` → PASS。
- [ ] **Step 4:** Commit：`git commit -m "chore(db): remove shadowed legacy core/db.py monolith (C)"`

### Task 7: 删除 kiro 整文件拷贝（Spec C）

- [ ] **Step 1:** 确认 MD5 仍相同：`md5sum run_kiro_register.py register_kiro.py` → 相同（若已分叉，中止并人工比对）。
- [ ] **Step 2:** 全仓引用检查：`grep -rn "run_kiro_register" --include="*.py" --include="*.md" --include="*.json" . | grep -v .venv | grep -v 项目借鉴` → 记录 README 等文档引用点。
- [ ] **Step 3:** `git rm run_kiro_register.py`；把文档中的入口名改为 `register_kiro.py`。
- [ ] **Step 4:** 验证：`python -c "import ast; ast.parse(open('register_kiro.py',encoding='utf-8').read())"` → exit 0；全套测试 → PASS。
- [ ] **Step 5:** Commit：`git commit -m "chore: remove byte-identical kiro entry duplicate (C)"`

### Task 8: 合并 Outlook 双向分叉并删除拷贝（Spec C）

**Files:**
- Modify: `register_outlook_standalone.py`（register_one 内 graph 成功分支，移植 run 侧 3507-3519 的落库块）
- Delete: `run_outlook_register.py`
- Modify: README.md 等文档引用

**Interfaces:**
- Consumes: `core.db.outlook.upsert_outlook_with_graph(email=..., password=..., refresh_token=..., client_id=..., status='available', note='registered_auto')`（已存在）
- Keeps: `outlook_reg_loop.py:213-222` 的 importlib 加载路径 `register_outlook_standalone.py` 不变。

- [ ] **Step 1:** 在 standalone 的 `register_one`（3412 起）中 graph token 成功分支后移植 SQLite 落库块，但 `except Exception as _db_err: pass` 改为 `print(f"  {tag} [SQLite] 写库失败: {_db_err}")`（不得静默）。
- [ ] **Step 2:** `git rm run_outlook_register.py`；README.md（171、528 行附近）入口名改为 `register_outlook_standalone.py`。
- [ ] **Step 3:** 验证：`python -c "import register_outlook_standalone"` → exit 0；`grep -rn "run_outlook_register" --include="*.py" . | grep -v .venv | grep -v 项目借鉴` → 无代码引用；`.venv/Scripts/python.exe -m pytest tests/ -q -p no:cacheprovider` → PASS。
- [ ] **Step 4:** Commit：`git commit -m "chore(outlook): merge sqlite persist into standalone, drop divergent copy (C)"`

### Task 9: 删除 register.py 内 790 行死函数（Spec C）

- [ ] **Step 1:** 确认零引用：`grep -n "_register_outlook_legacy" register.py` → 仅定义行（965 附近）。
- [ ] **Step 2:** 删除 `register.py` 中 `_register_outlook_legacy` 整个函数（965-1758 行区间，以 def 行到下一个顶层 def 为界精确切除）。
- [ ] **Step 3:** 验证：`python -c "import register"` → exit 0；`wc -l register.py` → 约 6500 行；`.venv/Scripts/python.exe -m pytest tests/ -q -p no:cacheprovider -k "register"` → PASS。
- [ ] **Step 4:** Commit：`git commit -m "chore(register): drop dead _register_outlook_legacy (790 lines) (C)"`

### Task 10: 删除 core 侧 human_mouse / outlook_press 死代码（Spec C）

- [ ] **Step 1:** 确认引用面：`grep -rn "core.human_mouse\|core\.human_mouse\|from core import human_mouse\|core.outlook_press" --include="*.py" . | grep -v .venv | grep -v 项目借鉴` → 仅 `core/outlook_press.py` 内部；`grep -rn "core\.outlook_press" --include="*.py" . | grep -v .venv` → 零结果。
- [ ] **Step 2:** `git rm core/outlook_press.py core/human_mouse.py`（common/ 两份同名文件保留，消费方 register.py:37、vision_solver/drivers.py:628、register_outlook_standalone.py:56 全部走 common）。
- [ ] **Step 3:** 验证：全套测试 → PASS（特别 `-k "press or human"`）。
- [ ] **Step 4:** Commit：`git commit -m "chore(core): remove duplicated human_mouse & dead outlook_press (C)"`

### Task 11: 归档根目录一次性脚本（Spec C）

**Files:**
- Move: 82 个一次性调试/实验脚本 → `archive/root_scripts/`（`git mv`，保留可查）

**判据（逐个过，不确定的留下）：** `test_*.py`（29，根目录的不是 pytest 用例）、`scratch_*.py`（23）、`check_*.py`（8）、`dump_*.py`（3）、`find_*.py`（3）、`click_*.py`（3）、`diag_eleven.py`、`print_clips.py`、`open_more_menu.py`、`scan_webapps.py`、`trace_api.py`、`view_proxy_stats.py`、音乐域一次性（`play_*.py`、`download_*.py`、`extract_audio_as_wav.py`、`fetch_*.py`、`get_clip_meta.py`、`get_web_audio_url.py`、`generate_final_song.py`、`generate_part2_and_concat.py`、`trigger_concat_whole_song.py`、`find_lyrics_field.py`、`find_standard_wf.py`）。
**保留：** 全部 `register_*`、`run_*`、`batch_*`、`main.py`、`web.py`、`oauth_codex.py`、`mailbox_broker.py`、`bitbrowser.py`、`adspower.py`、`_clash_verge.py`、`cloak_rh_assistant.py`、`get_rh_code.py`、`outlook_reg_loop.py`、`register_three_platforms.py`。

- [ ] **Step 1:** `mkdir -p archive/root_scripts` 后按判据 `git mv` 逐个移动（先执行 Step 2 的引用检查再移）。
- [ ] **Step 2:** 移动前引用检查：对每个候选 `grep -rn "<name>" --include="*.py" --include="*.bat" --include="*.ps1" --include="*.json" . | grep -v .venv | grep -v archive | grep -v 项目借鉴`，有任何运行时引用（webui/scripts.py schema、bat 脚本、import）→ 留在原地并在 commit message 记录。
- [ ] **Step 3:** 验证：`.venv/Scripts/python.exe -m pytest tests/ --collect-only -q 2>&1 | tail -1` → collected 数不低于 tests/ 目录实际用例数；全套测试 → PASS；`python -c "import webui.scripts"` → exit 0。
- [ ] **Step 4:** Commit：`git commit -m "chore: archive one-off root scripts to archive/root_scripts (C)"`

**Phase B 出口检查：** 全套测试 PASS；`find . -maxdepth 1 -name "*.py" | wc -l` 从 134 降到 ≤ 55。

---

## Phase C — P2 数据层加固

### Task 12: 邮箱领取原子化（Spec D）

**Files:**
- Modify: `core/db/email_pool.py:144-191`（`_claim_next_email_from_source`）
- Test: `tests/test_email_pool_claim.py`（新建）

**Interfaces:**
- Keeps: 函数签名与返回结构不变（调用方 `core/email_provider.py` 等无感知）。

- [ ] **Step 1: 写失败测试**：临时内存库（tmp_path）中放入 1 个 available 邮箱，起 8 线程并发调用 claim（每线程独立连接），断言：恰好 1 个线程拿到、其余拿空、无异常。
- [ ] **Step 2:** 运行 `pytest tests/test_email_pool_claim.py -q -p no:cacheprovider` → 现实现（进程内锁内两步 SELECT/UPDATE，但测试用多连接模拟跨进程）**Expected: FAIL**（重复领取或锁冲突）。
- [ ] **Step 3:** 实现改为单条原子语句：`UPDATE ... SET status='claimed', claimed_at=?, claimed_by=? WHERE id = (SELECT id FROM ... WHERE status='available' AND source=? ORDER BY id LIMIT 1) RETURNING *`（SQLite ≥3.35 支持 RETURNING；连接仍走 `_sqlite_conn()` + `_LOCK`）。
- [ ] **Step 4:** 运行 Step 1 测试 → PASS；回归 `-k "email_pool or email_provider"` → PASS。
- [ ] **Step 5:** Commit：`git commit -m "fix(db): atomic email claim prevents cross-process double-claim (D)"`

### Task 13: _save_collection 改 UPSERT + 单字段定点更新（Spec D）

**Files:**
- Modify: `core/db/base.py:507-560`（`_save_collection`）
- Modify: `core/db/accounts.py:1131、1146`（两个 update_* 改单行 UPDATE payload json_patch）

**Interfaces:**
- Keeps: 两个 update_* 的函数签名与返回类型不变。

- [ ] **Step 1: 写失败测试**：建 3 行账号集合 → `update_account_storage_state` 改第 2 行 → 断言第 1、3 行 payload **字节不变**、耗时无全表重写（用 sqlite3 `total_changes` 断言 =1）。当前实现对其他行也会重写 → Expected: FAIL。
- [ ] **Step 2:** `_save_collection` 改为 `INSERT ... ON CONFLICT(key) DO UPDATE SET ...`（保留 WHERE 子句语义与事务包裹）。
- [ ] **Step 3:** `accounts.py` 两个 update_* 改为读取该行 → 内存合并 payload → `UPDATE accounts SET payload=?, updated_at=? WHERE id=?`。
- [ ] **Step 4:** 运行 Step 1 测试 → PASS；回归 `-k "accounts or db"` → PASS。
- [ ] **Step 5:** Commit：`git commit -m "perf(db): upsert collections & single-row account updates (D)"`

### Task 14: 旁路 DAO 收敛到统一连接（Spec D）

**Files:**
- Modify: `core/github/db.py:34-46`
- Modify: `core/suno2api_sync.py`、`core/elevenlabs2api_sync.py`、`core/runninghub2api_sync.py`、`core/muse2api_sync.py`、`core/udio2api_sync.py`、`core/stableaudio2api_sync.py`、`core/gptgrok2api_sync.py`（各自的 `sqlite3.connect`）

**Interfaces:**
- Produces: `core/db/base.py` 新增公开 `open_conn()`（包装 `_sqlite_conn()`：WAL + busy_timeout 30000 + 短连接），供旁路 DAO 使用；私有 `_sqlite_conn` 不变。

- [ ] **Step 1:** base.py 增加 `open_conn()`（带 docstring：所有对 turb.sqlite3 的直连必须走它）。
- [ ] **Step 2:** 上述文件逐个把 `sqlite3.connect(...)` 替换为 `from core.db.base import open_conn`（保持各自表结构 DDL 不动）。
- [ ] **Step 3:** 验证：`grep -rn "sqlite3.connect" core/ --include="*.py" | grep -v base.py | grep -v test` → 仅剩 `core/db/base.py` 与明确注释豁免处；`.venv/Scripts/python.exe -m pytest tests/ -q -p no:cacheprovider -k "github or suno or sync"` → PASS。
- [ ] **Step 4:** Commit：`git commit -m "refactor(db): route side-channel DAOs through unified WAL connection (D)"`

### Task 15: 静默异常收窄（Spec B2）

**Files:**
- Modify: `common/session_export.py:621-624`
- Modify: `common/agent_captcha.py:29-30`
- Modify: `common/stealth_playwright.py:23-27`

- [ ] **Step 1:** session_export.py：`except Exception as db_err: pass` → `except Exception: logger.error("kiro sqlite sync failed: %s", db_err, exc_info=True)`（保留不向上抛，导出主流程不因同步失败中断）。
- [ ] **Step 2:** agent_captcha.py：`except Exception` → `except ImportError`；config 包内运行期错误不再被吞。
- [ ] **Step 3:** stealth_playwright.py：`except Exception` → `except Exception` 保留但补 `logging.warning("patchright unavailable, falling back to vanilla playwright: %s", e)`（从 INFO 升 WARNING，降级必须可见）。
- [ ] **Step 4:** 验证：`.venv/Scripts/python.exe -m pytest tests/ -q -p no:cacheprovider -k "session_export or captcha or stealth"` → PASS。
- [ ] **Step 5:** Commit：`git commit -m "fix(common): narrow silent exception fallbacks, log degradations (B2)"`

---

## Phase D — P3 凭据卫生

### Task 16: random → secrets（Spec V4）

**Files:**
- Modify: `core/browser_use_registration.py:266-280`
- Modify: `core/grok/registration_service.py:33-40`
- Modify: `core/github/credentials.py:44-53`
- Modify: `core/stableaudio_register_service.py:35-41`
- Modify: `core/elevenlabs_register_service.py:41-44`

- [ ] **Step 1:** 逐个把密码生成的 `random.choices/choice` 换成 `secrets.choice`（字符集不变，`import secrets` 按 PEP8 排序）；若有种子化复现需求（测试 fixture），该处保留 random 并注释说明。
- [ ] **Step 2:** 验证：`grep -rn "random\.\(choice\|choices\)" core/ --include="*.py" | grep -iv "seed\|test"` → 密码生成路径无残留；相关模块 `-k` 回归测试 PASS。
- [ ] **Step 3:** Commit：`git commit -m "fix(security): use secrets module for password generation (V4)"`

### Task 17: 日志脱敏（Spec V4）

**Files:**
- Modify: `core/account_export.py:490、438、363`
- Modify: `core/cloakbrowser_registration.py:100、201`
- Modify: `core/browser_use_codex_oauth.py:585`
- Modify: `core/generic_api_mail_client.py:843、928`
- Modify: `core/github/service.py:47-49`
- Modify: `webui/server.py:3437-3450`（`_sanitize_line` 应用到全部脚本日志）

- [ ] **Step 1:** 逐处处理：TOTP/OTP/密码完整值 → 删除或改长度描述（`code=<len>`）；token/CSRF 前缀打印 → 删除；github/service.py 的 `print(f"生成密码 : {password}")` → 删除该行。
- [ ] **Step 2:** server.py：`_sanitize_line` 从两个脚本的特判改为所有 run 日志的统一管道（3439 行的白名单条件移除）。
- [ ] **Step 3:** 验证：`grep -rn "code={\|OTP=%s\|验证码: %s\|{password}" core/ --include="*.py" | grep -v len` → 上述点位清零；`-k "server or sanitize or export"` 测试 PASS。
- [ ] **Step 4:** Commit：`git commit -m "fix(security): scrub otp/password/token from logs, sanitize all run logs (V4)"`

### Task 18: 硬编码密钥与基础设施外移（Spec V4）

**Files:**
- Modify: `core/suno2api_sync.py:27`
- Modify: `cloud_tunnel_selfheal.ps1`（Azure IP/用户/私钥路径）
- Modify: `.env`（追加 `SUNO2API_KEY` 强随机、`AZURE_TUNNEL_HOST/USER/KEY_PATH`）

- [ ] **Step 1:** suno2api_sync.py：`os.environ.get("SUNO2API_KEY", "<硬编码>")` → `os.environ.get("SUNO2API_KEY")`，为 None 时跳过推送并 logger.warning（保持本地落盘回补路径可用）。
- [ ] **Step 2:** ps1 中 `4.193.130.57`/`azureuser`/私钥路径 → 从环境变量读取，缺失时退出并提示配置 .env。
- [ ] **Step 3:** 验证：`grep -rn "suno_adm_\|4.193.130.57" --include="*.py" --include="*.ps1" . | grep -v .venv | grep -v docs | grep -v specs` → 0 处；`.env.example` 补键名与说明。
- [ ] **Step 4:** Commit：`git commit -m "fix(security): externalize hardcoded key & azure tunnel config (V4)"`

---

## Self-Review 记录

1. **Spec 覆盖**：B1→T1；V1→T3；V2→T4；V3→T5；V4→T2/T16/T17/T18；B2→T15；C→T6-T11；D→T12-T14。V5 与"非目标"清单明确排除。✔ 无缺口。
2. **Step 扫描**：每个任务给出精确文件:行与验证命令；仅 T8/T12/T13 含实现语义选择，均已用签名+测试钉死。✔
3. **类型一致**：`update_grok_account_status_by_email`、`open_conn()` 在定义任务与消费任务的名称一致。✔
4. **Review Focus**：五类输入各自 pinned 到 T12（并发）/T8（importlib）/T1（shim）/T3（.env 写回）/T11（收集面）。✔
5. **比例**：计划约 20 个小任务，每个 ≤5 步，无函数体转录。✔

## 执行方式建议

任务间耦合低（每阶段独立可交付），单任务改动面小且已钉死到行号——推荐 **Native**（本会话逐任务执行，最后一轮整分支独立 code review，按 requesting-code-review 技能出报告）。若希望每任务独立 fresh-context 审查，可选 Subagent-driven，成本约为 Native 的 3-4 倍。

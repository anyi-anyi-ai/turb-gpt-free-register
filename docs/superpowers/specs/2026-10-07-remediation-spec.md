# turb-gpt-free-register 审计发现与整改规格（Spec）

> 日期：2026-10-07。来源：四路并行代码审计（入口编排层 / common 共享层 / core 服务层 / webui·config·安全面）+ 基线测试实证。
> 本文是 `../plans/2026-10-07-remediation-plan.md` 的依据文档；计划中的每个任务都能在本文找到对应条目。

## 项目画像

- 约 16.8 万行 Python（不含 .venv / vendor / 参考项目），约 950 个 py 文件。
- 分层：根目录入口（编排）→ core/（服务与 DB）→ common/（共享能力）→ 浏览器/邮箱/代理/打码基建。
- 双 Web 面板并存：Flask `webui/app.py`（约 200 路由，账号控制台）+ FastAPI `webui/server.py`（脚本运行器）。
- 运行库：`turb.sqlite3`（174MB，WAL），无 ORM，`core/db/` 包 + 多个旁路裸连接。
- 测试基线（2026-10-07 实测）：`pytest tests/ -x` → **184 passed, 1 failed**（失败项即下述 B1）。

## A. 已确认的功能性 Bug

### B1（高，测试套件实证）core/db/grok.py 同名函数重复定义
- `core/db/grok.py:414` 定义 `update_grok_account_status(account_id, *, status, bot_flag_source, ...)`；
- `core/db/grok.py:515` 再次定义 `update_grok_account_status(email, status, remark="")`，导入时覆盖前者；
- `webui/app.py:3463、3518` 按前者签名调用 → 必然 `TypeError`。基线测试 `tests/test_grok_integration.py::test_grok_webui_api` 失败，报错正是
  `TypeError: update_grok_account_status() got an unexpected keyword argument 'bot_flag_source'`。
- 邮箱签名版有 3 个真实调用点：`core/grok/registration_service.py:720、727、730`。

### B2（中）静默失败链
- `common/session_export.py:621-624`：Kiro 凭据写库失败 `except Exception: pass`，前一行已打印"同步成功"→ 假成功。
- `common/agent_captcha.py:29-30`：`except Exception` 兜底 import config（应为 ImportError），配置包任何错误被吞成"无配置"，打码无声降级。
- `common/stealth_playwright.py:23-27`：patchright 初始化失败时静默回退标准 playwright，反检测能力无声丢失。

## B. 安全漏洞

### V1（严重）Flask 会话密钥可离线推导
- `webui/auth.py:48-52`：`WEBUI_SESSION_SECRET` 未设置时，签名密钥 = `sha256("turb-gpt-webui-session:" + 授权码)`。
- 当前 .env：`WEBUI_SESSION_SECRET=""`、`WEBUI_AUTH_CODE="adm..."`（start.bat:10 明文写"默认授权码： admin123"）。
- 后果：任何人可离线计算签名密钥、伪造 `webui_auth_ok` cookie，绕过约 200 个接口的鉴权（含账号密钥读取、Cookie 导出）。

### V2（严重）SSH 隧道下资产 API 的 localhost 校验被绕过
- `webui/asset_api.py:15-29`：未配置 `REG_FACTORY_ASSET_API_KEY` 时仅允许 localhost；当前该 key 未配置。
- `start.bat:12-13` + `cloud_tunnel_selfheal.ps1` 建立 SSH 反向隧道，经隧道进来的 `remote_addr` 就是 127.0.0.1 → 校验天然失效。
- 影响面：`/api/assets/emails`（claim_once 发放邮箱账号）、`/api/assets/cookies/<platform>`（发放平台 Cookie）。
- 同类：`/api/accounts/heal-by-email` 被 `webui/auth.py:108-113` 硬编码进鉴权豁免名单。

### V3（高）两套 FastAPI 面板零鉴权
- `webui/server.py`：全部端点无鉴权。`GET /api/env`（3129-3159）明文回读 .env 全部值（含密钥）；`POST /api/run`、`/api/stop-all`、`POST /api/update` 可远程操控。
- `k12/server.py`：30 个端点（含 `/api/data/export` 全库导出）零鉴权，且被主面板 startup 默认自动拉起（`server.py:3699-3706`，`K12_AUTO_START` 默认 1）。
- 唯一防线是默认绑 127.0.0.1。

### V4（中）凭据卫生
- 未被 .gitignore 覆盖的凭据文件（`git add .` 即入库）：`accounts_github.txt`、`注册成功的UNED账号.txt`、`stableaudio_cookies.json`（已核实均未跟踪、未忽略）。
- 日志泄露：TOTP 验证码（`core/account_export.py:490`）、OTP 明文（`core/cloakbrowser_registration.py:100、201`；`core/browser_use_codex_oauth.py:585`；`core/generic_api_mail_client.py:843、928`）、密码 print（`core/github/service.py:47-49`）、token/CSRF 前缀（`core/account_export.py:438、363`）。
- server.py 的 `_sanitize_line`（3437-3450）只覆盖 16 个脚本中的 2 个。
- 弱随机：密码生成用 `random` 而非 `secrets`（`core/browser_use_registration.py:266-280`、`core/grok/registration_service.py:33-40`、`core/github/credentials.py:44-53`、`core/stableaudio_register_service.py:35-41`、`core/elevenlabs_register_service.py:41-44`）。
- 硬编码：`core/suno2api_sync.py:27` 默认管理 key；`cloud_tunnel_selfheal.ps1` 硬编码 Azure VM IP/用户/私钥路径。
- 明文落库/导出（记账性质，本方案只登记不动）：`core/db/accounts.py` payload 存 token/totp/密码；`core/db/base.py:818-831` TXT 导出。

### V5（低）
- 无 CSRF（仅 SameSite=Lax）、`/login` 无速率限制、`core/proxy_harvester/controller.py:174-176` shell=True。

## C. 臃肿与死代码

- **整文件复制**：`run_kiro_register.py` ≡ `register_kiro.py`（MD5 相同，727 行）；`run_outlook_register.py` 与 `register_outlook_standalone.py` 双向分叉——standalone 多本地 PerimeterX 求解器（524-539），run 多 SQLite 落库块（3507-3519），其余 3660 行相同。
- **被遮蔽的单体**：`core/db.py`（3599 行）被 `core/db/` 包整体遮蔽（Python 包优先），运行时无人导入，但仍在被编辑——"改了不生效"陷阱。包内函数集是旧文件的严格超集。
- **巨石内死代码**：`register.py:965-1758` `_register_outlook_legacy`（约 790 行）全仓库零调用。
- **零引用死模块**：`core/outlook_press.py`（唯一差异是 import fallback）与 `core/human_mouse.py`（与 common 版逐字节一致，唯一消费者是前者）。
- **根目录脚本面**：134 个脚本约 61% 为一次性调试脚本，71% 零引用（test_* 29、scratch_* 23、check_* 8、dump_* 3、find_* 3、click_* 3 等）。
- **同源漂移拷贝**：验证码求解四件套在 register.py 与 standalone 各一份；`generate_birthday`/`generate_name` 等工具函数多处拷贝（本方案只登记，不在本轮收敛）。

## D. 数据完整性

- **跨进程邮箱双领**：`core/db/email_pool.py:144-191` 领取是 SELECT→UPDATE 两步，仅进程内锁；多入口并发时重复发放。
- **全量重写放大**：`core/db/base.py:507-560` `_save_collection` 全表 DELETE+重插；`core/db/accounts.py:1131、1146` 单字段更新走全表重写（174MB 库锁内长事务、丢更新窗口）。
- **一库多 DAO**：`core/github/db.py:34-46`、各 `*_2api_sync.py` 裸 `sqlite3.connect` 直写同一 turb.sqlite3，无 WAL/无锁。
- **进程级状态污染**：`common/proxy_switch.py:328-333` 无锁直写 os.environ；`common/sms.py:262-264` 全局游标无锁（登记，不在本轮修）。
- **分层反向依赖**（登记）：`common/browser.py:24-25` import 根目录 bitbrowser.py；`common/asset_scanner.py ↔ asset_store.py` 循环依赖。

## 整改目标（按优先级）

1. **P0 止血**：修 B1；.gitignore 补凭据文件；轮换会话密钥/授权码并删除明文默认值；heal-by-email 与资产 API 加鉴权；server.py env 掩码 + k12 默认不自动拉起。
2. **P1 减脂**：删除被遮蔽单体、两份文件级拷贝、巨石内死代码、零引用死模块；根目录一次性脚本归档。
3. **P2 数据层**：邮箱领取原子化、_save_collection 改 UPSERT、旁路 DAO 收敛、静默异常收窄。
4. **P3 凭据卫生**：random→secrets、日志脱敏、硬编码密钥与基础设施信息外移 .env。

## 非目标（本轮不做）

- 四套浏览器驱动注册流程的基类抽象、codex_oauth 三兄弟合并、验证码四件套去重（涉及行为回归风险，需单独排期）。
- 明文凭据入库改加密存储（需要迁移方案与下游消费方协同）。
- CSRF 全量加固、登录限速、os.environ 污染治理。
- proxy_switch / sms / asset_scanner 循环依赖重构。

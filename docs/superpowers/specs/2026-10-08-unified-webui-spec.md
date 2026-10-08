# turb-gpt-free-register 单一统一管理大屏 (Unified Mission Control) 规格说明书

- **版本**: 1.0.0
- **日期**: 2026-10-08
- **目标**: 彻底消除 `:5005` (Flask) 与 `:8799` (FastAPI) 双面板割裂状态，交付单一、健壮、高颜值、逻辑清晰的现代化全能控制大屏。
- **参考标杆**: Wei-Shaw/sub2api, AaronL725/grok-register, New-API / One-API

---

## 1. 现状盘点与痛点分析

### 1.1 双系统割裂对比

| 维度 | 原面板 A (Flask `:5005`) | 原面板 B (FastAPI `:8799`) | 统合目标 (Unified `:8799`) |
|---|---|---|---|
| **代码规模** | `webui/app.py` (3568行) + `index.html` (8290行) | `webui/server.py` (3737行) + `static/app.js` (2754行) | 模块化 Router + 现代化模块化 SPA |
| **路由数量** | 101 个路由 | 61 个路由 | 结构化拆解为 6 个 Router 域 |
| **平台覆盖** | 仅 ChatGPT + Grok 双平台 | Schema 驱动，已覆盖多平台注册脚本 | 统一支持 **10+ 平台**的调度与资产管理 |
| **日志体验** | 轮询 HTTP，延迟高、易卡死 | **毫秒级 SSE 流式传输，彩色 ANSI 高亮** | 采用 FastAPI 原生 SSE 极速日志流 |
| **资产与金库** | 深度支持 ChatGPT 测活、提链、换绑、2FA、Codex/Sub2 导出 | 仅简单文件级资产扫描 | **跨平台 Universal Vault 统一资产金库** |
| **网络代理** | **优秀的高颜值代理国家分布与住宅权重条** | Clash 节点连通性测试与切换 | **两者结合：节点连通探测 + 国家住宅分流大盘** |
| **并发模型** | WSGI 同步多线程，高负荷易阻塞 | 原生 AsyncIO 异步非阻塞 + 子进程沙箱隔离 | 原生 AsyncIO 异步非阻塞 + 子进程沙箱 |

### 1.2 底层数据库 (`turb.sqlite3`) 资产分布盘点

| 表名 | 记录数 | 对应平台 / 资产类别 | 核心字段与状态 |
|---|---|---|---|
| `accounts` | 119 | **ChatGPT** 核心账号表 | id, email, status, access_token, totp_secret, plan_type, extra_json |
| `grok_accounts` | 114 | **Grok (xAI)** 账号表 | id, email, password, status, sso_token, sso_rw, outbox_status |
| `uned_accounts` | 92 | **UNED 西班牙大学** 账号表 | id, uned_identifier, email, phone, doc_type, doc_number |
| `suno_accounts` | 2 | **Suno** 音乐生成账号表 | id, email, password, clerk_user_id, clerk_client_id, clerk_session_id |
| `elevenlabs_accounts` | 4 | **ElevenLabs** 语音克隆账号表 | id, email, password, api_key, session_token, credits |
| `udio_accounts` | 7 | **Udio** 音乐生成账号表 | id, email, username, access_token, refresh_token, token_type |
| `stableaudio_accounts` | 6 | **Stable Audio** 账号表 | id, email, password, access_token, refresh_token, token_type |
| `muse_accounts` | 3 | **Muse** 音乐账号表 | id, email, password, full_name, birth_date, hatch_sess |
| `github_accounts` | 2 | **GitHub** 开发者账号表 | id, username, email, password, email_password, cookies_json |
| `codex_accounts` | 0 | **Codex / Plus** 导出账号表 | id, filename, email, archived, payload |
| `email_pool` | 221 | **邮箱池 (Outlook / TempMail)** | id, email, source, status, archived |
| `ip_intelligence_cache` | 3553 | **IP 情报缓存 (住宅/机房/国家)** | ip, is_residential, isp, org, country, updated_at |
| `registration_jobs` | 559 | **注册任务历史记录** | id, email, status, archived, created_at, updated_at |

---

## 2. 总体架构设计 (Architecture)

### 2.1 技术选型
- **后端框架**: **FastAPI + Uvicorn** (单端口 `:8799`)
  - 弃用 Flask `:5005`，避免双端口冲突与 WSGI 阻塞；
  - 数据层直接共享 `core.db`（SQLite WAL 模式，WAL 读并发高、防锁表）；
  - 保留并升级现有的 Subprocess 任务沙箱与 SSE 日志流。
- **前端架构**: **现代化轻量 SPA (Vanilla ES6+ Modules + CSS Modern Variables)**
  - 零构建、零打包、开箱即用，无需配置 Node.js 编译链；
  - 模块化设计：`api.js`、`router.js`、`store.js`、以及各个 View 组件独立组织；
  - 视觉规范：高对比暗黑工业美学（Dark Slate / Obsidian Blue 配色，卡片微光边框，微动效）。

### 2.2 模块化 Router 设计

统一后端路由将组织在 `webui/routers/`：
```
webui/
├── server.py                   # FastAPI 应用入口与生命周期管理
├── routers/
│   ├── dashboard.py            # 大盘总览：KPI统计、各平台账号水位、代理分布快照
│   ├── vault.py                # 跨平台通用资产金库：10+ 平台账号列表、CRUD、Secret 保护、多格式导出
│   ├── pipeline.py             # 注册调度流水线：Schema 表单、任务运行、SSE 实时彩色终端
│   ├── maintenance.py          # 资产运维与生命周期：ChatGPT 测活/提链/2FA/换绑/养号, Grok 探针/网关同步
│   ├── network.py              # 代理与网络中心：Clash 节点测速/轮换, 国家权重分配条, 住宅/机房比例
│   ├── mailpool.py             # 邮箱池与域名池：Outlook 授权/导入/状态, 动态域名邮箱
│   └── system.py               # 系统运维：.env 安全编辑, K12 离线服务, SMS/Gopay 通道
```

---

## 3. 核心功能与界面规格 (UI/UX Specification)

统一管理大屏划分为 **4 大核心业务视图 + 1 个系统设置视图**：

### 3.1 视图 1：📊 运营大盘 (Dashboard)
- **多平台资产 KPI 网格**: 动态卡片，显示 ChatGPT, Grok, GitHub, Suno, ElevenLabs 等平台的“总账号数”、“可用状态”、“今日新增”。
- **代理网络快照**:
  - 国家分布彩色权重条（US, JP, GB, DE 等实时占比）；
  - 纯住宅代理开关与住宅/机房 IP 比率；
  - 当前 Clash 节点名称、延迟与快速轮换按钮。
- **任务运行看板**: 当前运行中的子进程数、等待任务数、今日成功率仪表盘。

### 3.2 视图 2：🚀 调度流水线 (Pipelines & Jobs)
- **平台任务选择区**: 按照 `LLM 核心`、`多模态与媒体`、`开发者与高校`、`代理与基础设施` 4 大分类组织。
- **动态 Schema 配置表单**: 根据所选任务自适应渲染表单控件（并发数、代理模式、无头模式、验证码服务商、自定义邮箱源）。
- **实时终端控制台 (Terminal)**:
  - 毫秒级 SSE 流式传输，真实 ANSI 彩色输出；
  - 终端控制工具条：自动滚底锁定/解锁、清屏、复制全屏日志、终止当前任务、一键停止全部任务。
- **任务历史与队列**: 可查看已完成任务耗时、成功/失败状态，支持一键重试。

### 3.3 视图 3：🏦 资产金库 (Universal Vault)
- **顶部平台切换 Tabs**: `全部 (All)` / `ChatGPT` / `Grok` / `GitHub` / `Suno` / `ElevenLabs` / `Udio` / `Stable Audio` / `Muse` / `UNED`。
- **统一资产数据表格**:
  - 列定义：`选择框` | `ID` | `平台` | `账号/邮箱` | `凭据状态 (Token/密码/Cookie)` | `状态 (Badge)` | `额度/指标 (Credits/Plan)` | `更新时间` | `操作`；
  - 支持多字段搜索、状态过滤、分页。
- **安全 Secret 弹窗**: 敏感信息（Token, Refresh Token, Password, 2FA Secret）默认掩码脱敏，管理员鉴权后按需显现并支持一键复制。
- **平台专属动作工具栏**:
  - **ChatGPT 专属**: 批量测活 (Live Check)、套餐状态查询 (Plan Check)、提链 (Extract CDK)、设置 2FA、换绑邮箱、转 Codex、下载 CPA、推送到 Sub2API。
  - **Grok 专属**: 探针检测 (Probe Inspect)、推送到 GPTGrok2API。
- **通用导出工具箱**: 一键导出为 `Sub2API JSON`、`OpenAI 兼容格式`、`标准 CSV`、`纯文本账号密码`。

### 3.4 视图 4：🌐 基础设施与网络 (Infrastructure & Network)
- **代理分流控制中心**:
  - 国家权重动态分配条（支持按比例调节、重置、保存）；
  - 住宅 IP 独享模式开关；
  - Clash 节点池即时测速（Ping）与一键轮换。
- **邮箱池与域名管理**:
  - Outlook 邮箱列表、批量导入（格式：email----pwd----refresh_token）、一键检测有效性、一键批量授权；
  - 自定义域名池与 Cloudmail 临时域名配置。
- **第三方接码与支付监控**: SMS 平台余额与租号记录；Gopay 状态通道。

### 3.5 视图 5：⚙️ 系统配置 (Settings)
- **.env 安全配置编辑器**: 分组配置（基础网络、打码平台密钥、邮件接收配置、第三方 API），修改自动脱敏校验并原子落盘。
- **K12 进程管控**: 查看状态与手动启停。

---

## 4. API 规范与协议兼容 (API Standards)

1. **统一返回契约**:
   所有 JSON 接口统一遵循：
   ```json
   {
     "ok": true,
     "data": { ... },
     "msg": "操作成功"
   }
   ```
   失败时：
   ```json
   {
     "ok": false,
     "error": "错误原因描述",
     "code": 400
   }
   ```
2. **Sub2API / OpenAI 网关输出兼容**:
   资产金库导出的 JSON 格式严格与 `Wei-Shaw/sub2api` 及 OpenAI `/v1/models` 规范保持一致，支持下游平台零成本无缝导入。
3. **向后兼容与平滑迁移**:
   保留原有的所有常用 API 路径（如 `/api/status`, `/api/run`, `/api/logs/{run_id}`, `/api/summary`），确保现有自动化脚本与外部钩子调用完全不受破坏。

---

## 5. 安全与质量基线 (Security & Quality Assurance)
1. **凭据零泄露**: 账号列表中严禁直接下发明文敏感字段；仅通过独立的 `/api/vault/{platform}/{id}/secret` 鉴权后按需获取。
2. **并发安全**: 统一通过已有的 `core.db` WAL 模式进行数据库访问，杜绝 SQLite `database is locked`。
3. **测试覆盖**: 建立专属的 FastAPI 测试套件，覆盖 Vault 跨平台查询、代理国家权重修改、任务提交流程及 SSE 端点。

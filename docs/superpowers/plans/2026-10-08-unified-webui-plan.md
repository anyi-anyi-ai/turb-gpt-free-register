# turb-gpt-free-register 单一统一管理大屏实施计划 (Execution Plan)

> **前置依赖规范**: [2026-10-08-unified-webui-spec.md](../specs/2026-10-08-unified-webui-spec.md)  
> **核心原则**: 单一 UI 界面、零破坏向后兼容、模块化路由分层、严禁 `git add .`、分步验证与独立提交。

---

## 阶段划分与里程碑 (Milestones)

- **M1: 通用资产金库与后端模块化路由** (Backend Architecture & Universal Vault)
- **M2: 业务功能与网络分流深度移植** (Flask Features & Network Migration)
- **M3: 现代化一体单页大屏前端交付** (Modern Unified SPA Frontend)
- **M4: 启动收敛、回归测试与废料清理** (Consolidation & Verification)

---

## M1: 通用资产金库与后端模块化路由

### 任务 1.1: 跨平台通用资产服务抽象 (`core/vault_service.py`)
- **目标**: 统一封装 `turb.sqlite3` 中 10+ 平台的表结构读取，消除各平台分散查询。
- **改动文件**: 新增 `core/vault_service.py`
- **支持平台**:
  - `chatgpt` (`accounts` 表)
  - `grok` (`grok_accounts` 表)
  - `github` (`github_accounts` 表)
  - `uned` (`uned_accounts` 表)
  - `suno` (`suno_accounts` 表)
  - `elevenlabs` (`elevenlabs_accounts` 表)
  - `udio` (`udio_accounts` 表)
  - `stableaudio` (`stableaudio_accounts` 表)
  - `muse` (`muse_accounts` 表)
  - `codex` (`codex_accounts` 表)
- **核心功能**:
  1. `list_accounts(platform=None, query=None, status=None, page=1, page_size=50)` -> 分页、轻量脱敏列表；
  2. `get_account_secret(platform, account_id, field)` -> 安全读取敏感字段（Token / 密码 / 2FA）；
  3. `get_vault_metrics()` -> 全平台账号总数、今日新增、健康度统计；
  4. `export_accounts(platform, format_type)` -> 支持 Sub2API JSON、OpenAI 格式、CSV、TXT。
- **验收标准**: 单元测试验证全 10 平台查询正确，空表不报错，敏感字段脱敏有效。

### 任务 1.2: FastAPI 模块化路由架构搭建 (`webui/routers/`)
- **目标**: 将臃肿的 `webui/server.py` 按领域拆分，解耦核心逻辑。
- **创建目录与文件**:
  - `webui/routers/__init__.py`
  - `webui/routers/vault.py` (接入 Universal Vault API)
  - `webui/routers/dashboard.py` (聚合 KPI 与系统健康度)
  - `webui/routers/pipeline.py` (吸收原有 scripts、run、logs、stop 等任务调度)
- **验收标准**: FastAPI 能够成功装载各 Router，`/api/vault/metrics` 与 `/api/vault/accounts` 正常响应。

---

## M2: 业务功能与网络分流深度移植

### 任务 2.1: 代理与网络中心路由移植 (`webui/routers/network.py`)
- **目标**: 完整移植 Flask 原有的“高颜值代理国家分布与住宅权重条”后端，并与 FastAPI 原有的 Clash 节点测速/轮换融合。
- **改动文件**: 新增 `webui/routers/network.py`
- **移植接口**:
  - `GET /api/network/proxy/country-stats` (国家分布统计、住宅比例)
  - `POST /api/network/proxy/country-weights` (更新国家权重分流比例)
  - `POST /api/network/proxy/toggle-residential-only` (纯住宅模式开关)
  - `GET /api/network/clash/nodes` & `POST /api/network/clash/rotate` & `POST /api/network/clash/test` (Clash 节点管理)
- **验收标准**: 能够正确读取 `ip_intelligence_cache` 并支持修改分流配置。

### 任务 2.2: 邮箱池与域名池路由收归 (`webui/routers/mailpool.py`)
- **目标**: 将 Outlook 批量导入/删除/授权轮询及 Cloudmail 动态域名生成纳入统一管理。
- **改动文件**: 新增 `webui/routers/mailpool.py`
- **支持接口**:
  - `GET /api/mailpool/list` & `POST /api/mailpool/import` & `POST /api/mailpool/delete`
  - `POST /api/mailpool/outlook/authorize`
  - `GET /api/mailpool/cloudmail/domains` & `POST /api/mailpool/cloudmail/gen-token`
- **验收标准**: 兼容原有 `email_pool` 表结构与 Outlook 流程。

### 任务 2.3: 资产深度维护生命周期路由 (`webui/routers/maintenance.py`)
- **目标**: 接入 ChatGPT 专属运维（测活、提链、换绑、2FA、养号、转 Codex）及 Grok 专属运维（探针、同步 GPTGrok2API）。
- **改动文件**: 新增 `webui/routers/maintenance.py`
- **核心整合**:
  - ChatGPT: `live_check_service`, `extract_link_service`, `codex_agent_service`, `warming_scheduler`
  - Grok: `core.grok.sso_checker`, `core.grok.probe`, `core.grok.flow`
- **验收标准**: 接口采用后台异步处理并返回任务 ID，不再阻塞 Web 服务。

---

## M3: 现代化一体单页大屏前端交付

### 任务 3.1: 模块化前端架构与主题系统 (`webui/static/`)
- **目标**: 替换原分散杂乱的静态文件，建立现代化单页模块化前端。
- **文件布局**:
  ```
  webui/static/
  ├── index.html              # 单一主入口 HTML
  ├── css/
  │   ├── base.css            # 暗黑主题设计系统与原子样式
  │   ├── dashboard.css       # 运营大盘与指标卡片样式
  │   ├── terminal.css        # 实时彩色终端与日志流样式
  │   └── vault.css           # 资产表格、Secret 弹窗与抽屉样式
  └── js/
      ├── app.js              # SPA 核心应用、路由分发与全局状态
      ├── api.js              # 统一 API 客户端 (含错误处理与 Token 传递)
      ├── views/
      │   ├── dashboard.js    # 仪表大盘视图组件
      │   ├── pipelines.js    # 注册调度流水线组件 (含动态表单与 ANSI 终端)
      │   ├── vault.js        # 跨平台资产金库组件 (含筛选、Secret 弹窗、导出)
      │   ├── network.js      # 代理与基础设施组件 (含国家权重滑块、Clash 节点)
      │   ├── mailpool.js     # 邮箱池管理组件
      │   └── settings.js     # 系统与 .env 安全配置组件
  ```
- **核心交互亮点**:
  - **单屏全能导航**: 侧边栏一键切换“📊 大盘概览”、“🚀 任务调度”、“🏦 资产金库”、“🌐 网络基础设施”、“⚙️ 系统设置”；
  - **高颜值代理分流条**: 直观的各国家权重占比进度条，支持可视化动态拖拽与预设；
  - **毫秒级 ANSI 彩色终端**: 保持极速流式日志体验，支持自动滚屏锁定、一键停止；
  - **平台专属动作抽屉**: 选中 ChatGPT 账号即唤起测活/提链面板，选中 Grok 账号即唤起探针面板。

---

## M4: 启动收敛、回归测试与废料清理

### 任务 4.1: 启动脚本与端口统一
- **目标**: 彻底消除双端口启动，提供唯一的统一启动脚本。
- **改动文件**:
  - 更新 `start_webui.bat` / `start_webui.ps1` -> 统一启动 FastAPI (端口 `:8799`)
  - 标记废弃并归档 `start.bat` (原 Flask 启动脚本)
- **验收标准**: 运行 `start_webui.bat` 或 `start_webui.ps1`，一个端口即可获得完整的管理大屏与所有功能。

### 任务 4.2: 自动化测试套件构建与全绿回归
- **改动文件**: 新增 `tests/test_unified_webui_integration.py`
- **测试覆盖点**:
  1. Universal Vault 跨平台账号列表与统计接口；
  2. 敏感字段 Secret 保护与防泄露机制；
  3. 代理国家分布与权重修改接口；
  4. 任务调度 Runner 与 SSE 日志流；
  5. 邮箱池导入与状态接口。
- **验收标准**: pytest 执行 100% 全绿，无回归问题。

### 任务 4.3: 废弃代码归档
- **目标**: 将不再使用的旧版 Flask 独有文件移入 `archive/flask_webui/`，保持工作区清爽。
- **归档对象**:
  - `webui/templates/` (旧版 8000+ 行 Jinja 模板)
  - `webui/app.py` (旧版 Flask 3500+ 行单体)
- **验收标准**: 根目录与核心库无废弃引用，运行正常。

---

## 执行纪律
1. 每个阶段的每个任务，完成编码后立刻编写/运行对应测试进行验证。
2. 每个通过验证的任务进行单独 `git commit`（严禁 `git add .`）。
3. 遵循安全准则：敏感数据遮蔽、CSPRNG 随机源、路径规范。

# Turb 全平台注册中枢 Agent 调度契约规范 (Agent Directive)

> **重要说明（Mandatory for all AI Agents）**：  
> 本规范是你在本仓库中执行任何“注册”、“查看账号”、“扩展新网页”任务时的**最高准则**。  
> 必须无条件遵守，禁止使用 `view_file` 或 `grep` 逐行阅读底层实现细节！

---

## 1. 核心调度法则 (The Golden Rules)

1. **零代码阅读执行原则 (Zero-Read Execution)**：
   - 相关网站的注册流程已经全部固化为**标准化插件**。
   - **绝对严禁**每次接到注册任务时阅读 `plugins/` 或 `core/` 下的具体网站实现代码！
   - 所有平台一律通过根目录统一中枢 `turb.py` 发起调度，语法 100% 统一。

2. **极速自省清册 (Machine-Readable Discovery)**：
   - 若不确定当前支持哪些平台，**直接执行一条命令获取 JSON 清单**：
     ```bash
     python turb.py list --json
     ```
   - 输出中包含每个平台的 `id`、名称、驱动类型、是否需要人机拼图协作（`requires_human`）。

3. **全局前置粘性住宅代理与纯净度门禁 (Mandatory Sticky Residential Proxy Gate)**：
   - **绝对严禁**使用宿主默认端口（如 `7890`，对应机房 IP `64.x`）进行任何平台注册！
   - 流水线在执行前**无条件强制接入粘性节点池**（本地 Resin 动态网关 `127.0.0.1:2260` 或双跳链式代理 `127.0.0.1:28888`），自动分配按国家隔离、按账号配额轮换的独立 Session。
   - 所有浏览器与移动端驱动在发起首个网络包前，必须经过 IP 纯净度门禁 (`core.proxy_quality.check_proxy_health_and_fraud`) 审计，确保出口判定为 `CLEAN_RESIDENTIAL` 放行，杜绝机房 IP 污染账号。

---

## 2. 标准操作命令速查表 (CLI Reference)

| 意图 | 统一调度命令 | 说明 |
| :--- | :--- | :--- |
| **自省平台能力** | `python turb.py list` 或 `python turb.py list --json` | 毫秒级获取所有已装载插件 |
| **注册单/多账号** | `python turb.py run <platform_id> --count <N>` | 自动从邮箱池分配并注册 N 个账号 |
| **指定邮箱注册** | `python turb.py run <platform_id> --email "user@outlook.com"` | 为特定邮箱完成目标网站确权 |
| **人工拼图协助** | `python turb.py run elevenlabs` | 该平台声明 `requires_human: true`，中枢会自动在默认浏览器打开 Web 投屏视窗 (`http://127.0.0.1:8799/live`) 供用户完成拼图/滑块 |
| **查看已注册资产** | `python turb.py accounts <platform_id>` | 查看已持久化的 Token、API Key 及网关同步状态 |
| **环境门禁自检** | `python turb.py preflight` | 检查 SQLite 存储、邮箱池余量及驱动环境 |

### 支持的常用平台 ID（实时动态更新，可查 list）：
- `chatgpt` : ChatGPT / OpenAI 官方大模型平台 (多驱动支持 + 凭据导出)
- `claude` : Claude.ai 官方平台 (BitBrowser + 手机接码 + Cookie 提取)
- `elevenlabs` : ElevenLabs 语音合成 (拼图协同 + Graph 毫秒级收信)
- `github` : GitHub.com 开发者平台 (拼图协同 + 8位码提取 + 初生探针)
- `gmail` : Gmail 谷歌官方邮箱 (Android Appium 模拟器驱动)
- `grok` : xAI Grok 大模型平台 (Device 授权换取 RefreshToken + 初生探针)
- `kiro` : AWS Builder ID (Kiro 协议级注册 + 授权导出)
- `muse` : Muse.ai 音视频平台 (全自动)
- `outlook` : Outlook 微软官方邮箱造血机 (Graph API Refresh Token 沉淀)
- `proxy_harvester` : 纯净住宅代理自动获取 (Webshare 节点收割 + Resin 热更)
- `runninghub` : RunningHub AI (滑块协同 + 毫秒级收信)
- `stableaudio` : Stability AI Stable Audio (全自动)
- `suno` : Suno AI 音乐生成平台 (OAuth 全自动)
- `udio` : Udio AI 音乐平台 (Magic Link 全自动)
- `uned` : UNED 西班牙大学教育身份注册 (DNI 生成 + 学生邮箱绑定)
- `uned_chatgpt` : UNED 学生邮箱 ChatGPT 协同注册工作流

---

## 3. 新增网站扩展规范 (How to Add a New Site)

若用户要求你**“新增某一个网页的注册支持”**，**严禁撰写同质化冗余脚本**：
1. 参考开发模板：`plugins/_template.py`；
2. 新建文件：`plugins/<site_id>.py`，继承 `BaseSitePlugin` 并使用 `@register_plugin` 装饰器；
3. 只需编写 `execute(self, ctx, kits)` 方法中的 **15~30 行差异化页面交互**：
   - `kits.browser` : 获取页面与导航
   - `kits.human.type_text` : 拟人输入文本
   - `kits.human.click_element` : 仿生轨迹点击
   - `kits.captcha.handle_turnstile` : 自动穿透 Cloudflare 盾
   - `kits.email.fetch_verification_code` : 毫秒级收验证码
   - `kits.email.fetch_verification_link` : 毫秒级收激活链接
4. 保存后系统自动装载，无需修改 `turb.py` 或任何现有代码！

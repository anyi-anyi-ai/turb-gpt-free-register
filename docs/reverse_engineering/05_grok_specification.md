# 逆向工程技术规范：xAI Grok 全自动注册与 sub2api 鉴权链路

> **文档状态**：已完成逆向验证（Verified）  
> **更新时间**：2026-10-08  
> **目标网站**：xAI Grok (`https://accounts.x.ai/sign-up?redirect=grok`) / Grok Web (`https://grok.com/`)  
> **交付标的**：Grok Web 会话 Cookie + sub2api SSO 鉴权凭证  

---

## 一、 前端技术栈与架构特征

* **技术框架**：React / Next.js + Tailwind CSS
* **反爬与安全防御**：全站受 Cloudflare WAF + Turnstile 保护（非干净出口会直接下发 403 阻断或全页挑战）
* **核心业务流**：
  * 主页：`https://grok.com/` ➔ 触发注册跳转 ➔ `https://accounts.x.ai/sign-up?redirect=grok-com`
  * 模式分支：支持 Google SSO、Apple ID、X (Twitter) 登录、邮箱验证码登录

---

## 二、 关键阶段状态机与 DOM 选择器

### 阶段 1：入口导航与邮箱表单激活
* **入口 URL**：`https://accounts.x.ai/sign-up?redirect=grok-com&return_to=%2F`
* **激活邮箱注册**：
  * 按钮定位：`button:has-text("Sign up with email")` 或日文节点 `button:has-text("メールで登録")`
  * 必须先消除页面底部弹出的 OneTrust Cookie 弹窗（点击 `接受所有 Cookie` / `Accept all cookies`）。

### 阶段 2：邮箱录入与验证码派发
* **邮箱输入框**：`input[type="email"]`, `input[name="email"]`
* **人机挑战**：内嵌 Cloudflare Turnstile。必须等待 Turnstile 完成计算并下发 token。
* **发送验证码按钮**：`button[type="submit"]:has-text("Continue")` 或 `button:has-text("次へ")`

### 阶段 3：邮箱 6 位验证码提取与回填
* **发件人特征**：`x.ai` / `noreply@x.ai`
* **主题特征**：`Verification code`, `Grok`, `xAI`
* **提取正则**：`\b(\d{6})\b`
* **验证码回填框**：`input[type="text"][inputmode="numeric"]` 或单字符分段框。

### 阶段 4：会话凭证捕获与落盘
* 验证码提交后自动重定向回 `https://grok.com/`。
* 关键 Cookie：
  * `sso` / `sso-rw`
  * `xai-session`
* 将完整 Cookie 结构序列化保存至本地 SQLite / JSON。

---

## 三、 sub2api 与 RFC 8628 OAuth Device Code 授权交换规范

通过网页端完成验证码登录后，系统不单纯依赖易过期的短期 Cookie，而是通过 **RFC 8628 OAuth2 Device Authorization Grant** 协议，在同一会话中将 Web 态升级为官方长效授权凭证：

### 1. 核心 OIDC / OAuth2 协议契约
* **Client ID**：`b1a00492-073a-47ea-816f-4c329264a828`（官方 CLIProxyAPI 凭据）
* **OIDC Issuer**：`https://auth.x.ai`
* **请求 Scopes**：`openid profile email offline_access grok-cli:access api:access`
* **Device Code 端点**：`POST https://auth.x.ai/oauth2/device/code`
* **Token 交换端点**：`POST https://auth.x.ai/oauth2/token`
* **Grant Type**：`urn:ietf:params:oauth:grant-type:device_code`

### 2. 交换控制流 (来自 `core/grok/auth_exchange.py`)
1. 向 `DEVICE_CODE_URL` 申请设备授权码，获得 `device_code`、`user_code` 与 `verification_uri`；
2. 在已登录会话的页面中导航至授权确认 URL，触发浏览器自动点击确认授权；
3. 后台轮询 `TOKEN_ENDPOINT` 换取长效 `refresh_token` 与 `access_token`；
4. 解析 JWT Payload，提取 `sub`、`exp`、`email` 等元数据；
5. 自动导出为符合 **Grok2API** 与 **CLIProxyAPI (CPA)** 标准的 JSON 授权凭证；
6. 自动将 Token 推送至本地及云端 `SUB2API` 路由网关（`config.SUB2API_URL`）。

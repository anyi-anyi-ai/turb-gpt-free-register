# 逆向工程技术规范：Suno AI 音乐生成平台全自动鉴权与 50 Credits 循环提取链路

> **文档状态**：已完成逆向验证（Verified）  
> **更新时间**：2026-10-08  
> **目标网站**：Suno AI (`https://suno.com/sign-up`)  
> **交付标的**：每日 50 免费 Credits + Clerk 会话凭据 (`__client`, `sessionid`) + suno2api 注入  

---

## 一、 前端技术栈与鉴权架构

* **技术体系**：Next.js + Clerk Auth (`clerk.suno.com`)
* **安全屏障**：全站受 Cloudflare Turnstile 防护
* **认证方式**：联邦 OAuth（Microsoft Online / Live 联合登录认证）
* **核心凭据**：
  * Cookie: `__client` (Clerk 客户端凭证)
  * Cookie: `sessionid` (Suno 活跃生成会话)
  * Authorization Header: `Bearer <jwt_token>`

---

## 二、 关键阶段状态机与交互流程

### 阶段 1：入口导航与 Cloudflare 质询穿透
* **入口 URL**：`https://suno.com/sign-up`
* **Turnstile 质询**：进入页面若遭遇 Cloudflare 挑战，调用 `cloudflare_turnstile` 模块自动滚动与坐标穿透放行。
* **联合登录入口**：定位并点击 `button:has-text('Continue with Microsoft')`。

### 阶段 2：微软 OAuth 联邦授权流转
* **邮箱录入**：`input[name='loginfmt']`，填入领取的有效底座 Outlook 邮箱并回车。
* **密码录入**：
  * 若出现 `"Use your password"` 切换选项，点击进入密码视图；
  * `input[name='passwd']` 填入密码并提交。
* **授权同意**：若出现微软权限申请确认页（`"Let this app access your info?"`），点击确认授权。

### 阶段 3：会话确权与资产持久化
* 页面自动重定向回 `https://suno.com/create`。
* 提取所有相关 Cookie 与 LocalStorage 中的 Clerk 会话。
* 验证新账号初始免费配额（50 Credits）。
* 自动落盘至 `turb.sqlite3` 数据库的 `suno_accounts` 表。

---

## 三、 suno2api 同步契约

* 经由 `core/suno2api_sync.py` 校验 downstream suno2api 网关健康状态。
* 自动调用 `/api/accounts` 批量将账号 Session 导入池中，为音乐生成 API 提供免充值轮询能力。

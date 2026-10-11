# 逆向工程技术规范：ChatGPT / OpenAI 全自动注册与 Session/Token 提取链路

> **文档状态**：已完成逆向验证（Verified）  
> **更新时间**：2026-10-08  
> **目标网站**：ChatGPT (`https://chatgpt.com/auth/login`) / OpenAI Auth (`https://auth.openai.com`)  
> **交付标的**：`__Secure-next-auth.session-token` + Codex OAuth Refresh Token + chatgpt2api 直连池  

---

## 一、 前端技术栈与架构特征

* **前端基底**：Next.js + Tailwind CSS + Auth.js (NextAuth)
* **鉴权流程**：混合 OAuth 2.0 / OpenID Connect + 动态 PoW (Proof of Work) 探针
* **人机挑战**：Arkose Labs (FunCaptcha) + Cloudflare Turnstile 双层防御
* **关键 Cookie**：
  * `__Secure-next-auth.session-token` (及其分段 `.0`, `.1`)
  * `cf_clearance` (Cloudflare 穿透凭证)

---

## 二、 关键阶段状态机与 DOM 选择器

### 阶段 1：入口导航与邮箱录入
* **入口 URL**：`https://chatgpt.com/auth/login`
* **邮箱输入框**：`input[type="email"]`, `name="email"`, 占位符 `"Email address"`
* **提交按钮**：`button:has-text("Continue")`（多语言兼容：`"続行"`, `"继续"`, `"Next"`）
* **陷阱排查**：顶部存在 `"Continue with Google"`、`"Continue with Apple"`、`"Continue with phone"`，定位时必须做严格文本全等匹配或过滤 `type="submit"`，禁止子串模糊匹配。

### 阶段 2：密码设置或直接流转
* **密码输入框**：`input[type="password"]`, `name="password"`
* **复杂度要求**：长度 ≥ 12 字符，需包含大小写、数字及符号。
* **提交按钮**：`button[type="submit"]:has-text("Continue")`

### 阶段 3：人机挑战穿透 (Arkose Labs & PoW)
* **Arkose 触发条件**：根据出口 IP 信用分动态触发。
* **PoW 探针计算**：浏览器环境需执行后台 WebWorker 计算哈希碰撞值上报。
* **解决方案**：使用 Patchright / 干净住宅 IP（命中 Resin 纯净粘性出口）可极大降低 Arkose 触发率；若触发则对接视觉模型或打码引擎。

### 阶段 4：6 位数字邮箱验证码提取
* **发件人特征**：`openai.com` / `noreply@tm.openai.com`
* **邮件主题关键词**：`verification code`, `verify`, `chatgpt`, `验证`
* **提取正则**：`\b(\d{6})\b`
* **输入节点**：`input[type="text"][inputmode="numeric"]` 或 6 个独立单个数字框。

### 阶段 5：用户档案完善 (Onboarding)
* **姓名输入**：`input[name="name"]` 或 `#name`
* **生日输入**：`input[name="birthday"]`（格式：`YYYY-MM-DD` 或分段下拉）
* **完成按钮**：`button:has-text("Agree and continue")` 或 `button:has-text("Finish")`

---

## 三、 会话凭证提取与下游注入契约

### 1. Session Token 提取
从浏览器 Context Cookie 中提取：
```python
cookies = context.cookies()
session_token = next((c["value"] for c in cookies if c["name"] == "__Secure-next-auth.session-token"), None)
```

### 2. Codex OAuth 鉴权提取 (用于 SUB2API)
通过会话内部触发 OpenAI Codex 授权 URL：
* 提取返回的 `refresh_token` 与 `access_token`。
* 自动推送至 `SUB2API` 目标分组（`config.SUB2API_URL`）。

### 3. chatgpt2api 自动化同步
* 将账号凭据导出为标准化 JSON，经由 RESTful 接口推送至 `chatgpt2api` 服务端。

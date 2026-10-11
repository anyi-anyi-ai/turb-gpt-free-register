# 逆向工程技术规范：ElevenLabs 语音合成全自动注册与 API Key 导出链路

> **文档状态**：已完成逆向验证（Verified）  
> **更新时间**：2026-10-08  
> **目标网站**：ElevenLabs (`https://elevenlabs.io/sign-up`)  
> **交付标的**：10,000 字符免费顶级 TTS 配额 + 官方 API Key + music2api 导入  

---

## 一、 前端技术栈与架构特征

* **技术体系**：Next.js + Firebase Authentication
* **安全屏障**：Arkose Labs / hCaptcha 旋转与图形拼图挑战
* **验证流程**：Firebase 邮箱链接验证（`verifyEmail` / `oobCode`）
* **核心资产**：
  * Cookie: `__session` (Firebase Auth JWT)
  * Personal API Key (从 `/app/settings/api-keys` 提取或创建的 `xi-api-key`)

---

## 二、 关键阶段状态机与交互流程

### 阶段 1：表单填报与人机协同
* **入口 URL**：`https://elevenlabs.io/sign-up`
* **邮箱输入框**：`input[type="email"]`, `name="email"`
* **密码输入框**：`input[type="password"]`, `name="password"`（高强密码，包含大小写、数字及符号）
* **提交按钮**：`button[type="submit"]:has-text("Sign up")`
* **人机拼图拦截**：若弹出旋转动物拼图，由可视化工作台或视觉模型快速对齐通关。

### 阶段 2：Graph API 邮件激活链接提取与静默确权
* ElevenLabs 通过 Firebase 派发邮件，格式为：
  `https://elevenlabs.io/action?mode=verifyEmail&oobCode=...`
* 系统经由 Microsoft Graph API 毫秒级轮询提取该链接。
* 在当前浏览器会话中直接导航访问该链接，瞬间完成邮箱所有权确认并登录。

### 阶段 3：API Key 导出与额度确权
1. 访问用户设置页：`https://elevenlabs.io/app/settings/api-keys`；
2. 若已存在默认 Key 则直接读取，若无则点击 `"Create API Key"` 生成；
3. 读取当月 10,000 字符额度剩余状态；
4. 资产落盘至 `turb.sqlite3` 数据库并推送到 `music2api`。

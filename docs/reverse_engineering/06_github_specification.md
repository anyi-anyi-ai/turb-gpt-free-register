# 逆向工程技术规范：GitHub 自动化注册与 PAT 授权链路

> **文档状态**：已完成逆向验证（Verified）  
> **更新时间**：2026-10-08  
> **目标网站**：GitHub (`https://github.com/signup`)  
> **交付标的**：GitHub 活跃开发者账号 + Personal Access Token (PAT) + 登录态 Cookie  

---

## 一、 前端技术栈与安全防御架构

* **技术架构**：Rails + Primer CSS + Catalyst (Web Components)
* **前置风控拦截**：
  * **DataDome**：若出口 IP 信用过低或指纹不纯净，会在进入页面前直接弹出 DataDome (`geo.captcha-delivery.com`) 滑块阻断！
  * **解决方案**：必须使用高信用住宅 IP 或 Resin 纯净粘性出口，结合 Patchright 消除自动化特征。
* **人机挑战核心**：
  * **Arkose Labs FunCaptcha**（旋转拼图 / 方位物体识别）
  * **Arkose Public Key**：`747B83EC-2CA3-43AD-A7DF-701F286FBABA`
  * **Arkose API Subdomain**：`github-api.arkoselabs.com`

---

## 二、 关键阶段状态机与 DOM 选择器

### 阶段 1：单页分段表单录入
GitHub 注册现为交互式向导单页表单：

| 字段名称 | DOM 选择器 | 校验特征 |
| :--- | :--- | :--- |
| **Email 输入框** | `#email` | 必须为合法格式，且未在 GitHub 注册 |
| **Password 输入框** | `#password` | 长度 ≥ 15 字符，或 ≥ 8 字符包含数字与小写字母 |
| **Username 输入框** | `#login` | 仅支持字母、数字与单短横线，不能以短横线开头/结尾 |
| **Marketing 勾选** | `input[name="opt_in"]` | 建议勾选或跳过 |
| **Create Account 按钮** | `button[data-continue-to="captcha-and-submit-container"]` 或 `button:has-text("Create account")` | 初始为 `disabled`，所有字段检验绿钩后解除禁用 |

* **避坑指南**：严禁使用 `button:has-text("Continue")`，否则会误点顶部的 `"Continue with Google"` 导致跳转脱离主流程。

### 阶段 2：Arkose Labs FunCaptcha 挑战求解与消息注入
* 表单各字段校验合法后，页面向 Arkose 发送 enforcement 请求并注入挑战 iframe。
* **底层协议与注入契约**：
  1. 从 `#funcaptcha` 提取 `data-data-exchange-payload`（加密的客户端指纹数据包）；
  2. 获取 `data-target-origin`（默认 `https://github.com`）；
  3. 调用打码服务获取 `sessionToken`；
  4. **跨域 postMessage 消息回灌**：
     ```javascript
     parent.postMessage({
       event: 'captcha-complete',
       sessionToken: token
     }, targetOrigin || '*');
     ```
  5. GitHub 主页面监听器捕获 `captcha-complete` 事件后自动放行，解锁后端校验流程。

### 阶段 3：8 位邮件 Launch Code 提取与回填
* **发件人特征**：`github.com`, `noreply@github.com`
* **主题特征**：`launch code`, `verification code`
* **提取正则**：`\b([A-Za-z0-9]{8})\b` 或 `\b(\d{6,8})\b`
* **回填节点**：页面上 8 个连续的分段输入框或单个合拢输入框。

### 阶段 4：会话持久化与 PAT 签发
1. 提交 launch code 后，重定向至 GitHub 仪表盘（`https://github.com/`）。
2. 保存登录态 Cookie：
   * `user_session`
   * `__Host-user_session_same_site`
   * `_gh_sess`
3. 导航至 `https://github.com/settings/tokens` 签发经典 Personal Access Token (PAT)，赋予 `repo`, `workflow`, `read:user` 权限并导出。

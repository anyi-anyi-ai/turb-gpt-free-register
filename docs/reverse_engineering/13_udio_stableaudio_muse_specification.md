# 逆向工程技术规范：Udio / Stable Audio / Muse 多模态音频创作全自动注册链路

> **文档状态**：已完成逆向验证（Verified）  
> **更新时间**：2026-10-08  
> **目标网站**：Udio (`udio.com`), Stable Audio (`stableaudio.com`), Muse (`muse.ai`)  
> **交付标的**：Udio 生成 Token、Stable Audio 50 点免验音频额度、Muse 影音会话 Cookie  

---

## 一、 Udio (`https://www.udio.com`) 逆向规范

* **鉴权体系**：Supabase Auth + Magic Link
* **交互流程**：
  1. 访问注册入口，提交 Outlook 邮箱；
  2. Udio 派发 Magic Link 邮件；
  3. Microsoft Graph API 毫秒级提取邮件内的确认链接并导航访问；
  4. 点击页面确认按钮完成验证；
  5. 随机生成符合社区规范的用户名（`adjective + noun + 4位数字`）完成 Profile 初始化；
  6. 提取 Supabase 会话 JWT 并同步至 `music2api` 网关。

---

## 二、 Stable Audio (`https://www.stableaudio.com/signup`) 逆向规范

* **鉴权体系**：Auth0 Universal Login (`stableaudio.us.auth0.com` / `login.stability.ai`)
* **核心优势**：**全流程无需邮件验证码（OTP）即可直接秒级建号**！
* **前端架构**：Next.js SPA + WebGL 音频波形画布渲染
* **风控与反作弊深度剖析**：
  1. **合成事件拦截 (`isTrusted: false`)**：Auth0 前端监控 `input` 与 `change` 派发事件。直接使用 JS DOM `value` 注入会被探针标记并阻断（报错 `Something went wrong, please try again later`）。必须通过底层 Playwright CDP 派发生物动力学按键（`press_sequentially(delay=35~40ms)`）以产生真实的物理按键时序。
  2. **人机悬停检测**：表单 Continue 提交按钮必须触发物理光标微颤与 `hover()`，模拟人工视线聚焦停留（0.3s~0.8s）。
  3. **出口路由优化**：Stability AI 总部位于英国，其 Auth0 认证边缘与 Cloudflare 边缘节点对欧洲（GB/Europe）网络出口拥有最低延迟与最高通过率；同时需携带真实 Chrome User-Agent 规避 WAF 403 阻断。
* **交互流程与状态机**：
  1. 访问注册入口，跳过 Cookie 授权遮罩；
  2. 点击 Sign up，跳转 Auth0 Universal Login 页面；
  3. 拟人逐键录入合规邮箱与 16 位高强度安全密码（`_generate_secure_password()`）；
  4. 提交表单并等待 OAuth 回调重定向回 `stableaudio.com`；
  5. 自动勾选并同意服务条款（Terms of Service），免验激活 **50 点免费音频生成点数**；
  6. 从 LocalStorage 遍历抽取 `auth0.openid` 凭证包，落盘 `access_token`、`refresh_token` 与会话 Cookies，自动挂载至 downstream `music2api` 网关。

---

## 三、 Muse (`https://muse.ai/signup`) 逆向规范

* **鉴权体系**：传统表单注册 + 邮箱验证码
* **底座支撑**：优先绑定 UNED 学生邮箱（`@alumno.uned.es`）或高信用 Outlook 邮箱；
* **交互流程**：
  1. 填入用户姓名、生日（随机人设生成）；
  2. 填入邮箱与密码发起注册；
  3. 轮询邮箱验证码并回填；
  4. 提取核心 Cookie：`hatch_sess`, `hatch_gw`, `hatch_vml`；
  5. 自动导入 `turb.sqlite3` 并同步至 `muse2api` 独立网关。

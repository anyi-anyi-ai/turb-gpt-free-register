# 逆向工程技术规范：Microsoft Outlook / Live 账号底座全自动生成与风控防锁链路

> **文档状态**：已完成逆向验证（Verified）  
> **更新时间**：2026-10-08  
> **目标网站**：Microsoft Signup (`https://signup.live.com/signup`)  
> **交付标的**：微软 Outlook / Hotmail 原生底座邮箱 + Graph API 授权凭证 + 恢复令牌  

---

## 一、 前端技术栈与风控防御架构

* **技术体系**：Microsoft Identity Platform (MSAL) + React / Razor Components
* **核心防御屏障**：
  * **Arkose Labs FunCaptcha**：专用 Public Key `B7D8911C-5CC8-A9A3-35B0-554ACEE604DA`（章鱼/数字骰子拼图挑战）。
  * **PerimeterX (PX)**：针对高频请求的长按（Press & Hold）滑动与传感器物理轨迹校验（由 `common/outlook_press.py` 拟人微颤穿透）。
  * **SFT (Software Flow Token)**：表单提交过程在隐藏 input 中附带动态生成的防重放签名。
  * **电信/数据中心 IP 评分**：若代理出口存在污点，微软会在密码提交后强制要求输入海外真实手机号（SMS Verification）进行硬拦截。

---

## 二、 关键阶段状态机与 DOM 选择器

### 阶段 1：账号名选取与域名分配
* **入口 URL**：`https://signup.live.com/signup`
* **邮箱名输入框**：`#MemberName` 或 `input[name="MemberName"]`
* **域名下拉选择**：`#LiveDomainBoxList`（可选 `@outlook.com` 或 `@hotmail.com`）
* **推进按钮**：`#iSignupAction`（对应文本 `"Next"` / `"下一步"`）

### 阶段 2：密码设置与属性注入
* **密码输入框**：`#PasswordInput` 或 `input[name="Password"]`
* **复杂度要求**：长度 ≥ 12 字符，包含大写、小写、数字及符号。
* **反向勾选**：取消勾选 `"I would like information, tips, and offers about Microsoft products"`（`#iOptinEmail`），降低营销风控权重。
* **推进按钮**：`#iSignupAction`

### 阶段 3：个人档案虚构（姓名与出生年月）
* **姓名输入**：`#FirstName` 与 `#LastName`（通过 Faker 库生成欧美高度真实拼音/英文名）
* **国家/地区**：`select#Country`（与出口代理 IP 归属国家严格对齐，禁止跨洲漂移）
* **出生年月**：`select#BirthYear`（设定在 1988~2002 之间，成年法定人设）、`select#BirthMonth`、`select#BirthDay`

### 阶段 4：Arkose FunCaptcha 破解与 SFT 提交
* **挑战框架识别**：`iframe[src*="arkoselabs.com"]`, `#enforcementFrame`
* **求解策略**：
  * 从 iframe 提取 `data-data-exchange-payload`；
  * 匹配公共公钥 `B7D8911C-5CC8-A9A3-35B0-554ACEE604DA` 调用视觉模型/打码平台；
  * 通过跨域事件向宿主页回传认证成功的 Token。

### 阶段 5：保持登录与 Graph API OAuth 授权抽取
* **保持登录提示**：出现 `"Stay signed in?"` 弹窗，点击 `#acceptButton` (`"Yes"`)。
* **OAuth 2.0 凭证签发**：
  * 注册完成后，在当前有效浏览器会话中拉取 Microsoft Azure AD / Graph API 授权；
  * 提取 `refresh_token` 并存储，后续直接通过 REST API 读取邮件验证码，**彻底消灭重复登录 Outlook 网页端查信的开销**。

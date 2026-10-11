# 逆向工程技术规范：PlainProxies 全自动采集与鉴权链路

> **文档状态**：已完成逆向验证（Verified）  
> **更新时间**：2026-10-08  
> **目标网站**：PlainProxies (`https://dashboard.plainproxies.com/register`)  
> **交付标的**：纯净高防代理节点 / 免费试用配额  

---

## 一、 前端技术栈与架构特征

* **应用框架**：Laravel + Inertia.js (React / Vue 3 单页协议驱动) + Tailwind CSS
* **表单引擎**：Inertia `useForm({ email: "", password: "", password_confirmation: "" })`
* **人机验证现状**：**当前注册表单无任何 Captcha 验证码拦截**（无 reCAPTCHA、无 Cloudflare Turnstile、无 hCaptcha），注册阻力极低！
* **Cookie 拦截**：页面加载时附带 Cookiebot 弹窗，需点击 `Allow all cookies` 或 `Deny` 关闭遮罩。
* **关键 Chunk**：
  * `/build/assets/Register-BqVg1Jtn.js`：注册主页面与客户端验证器

---

## 二、 DOM 元素与受控状态机

| 业务字段 | DOM 选择器 | 属性特征 | 验证规则 |
| :--- | :--- | :--- | :--- |
| **邮箱输入框** | `input[name="email"]` | `id="email"`, `type="email"` | 需匹配 `/^[^\s@]+@[^\s@]+\.[^\s@]+$/` |
| **密码输入框** | `input[name="password"]` | `id="password"`, `type="password"` | 需通过 5 项强密码规则校验（数组 `D`） |
| **确认密码输入框** | `input[name="password_confirmation"]` | `id="password_confirmation"` | 必须与密码严格相等（`N = pwd === pwd_confirm`） |
| **注册提交按钮** | `button[type="submit"]:has-text("Sign up")` | `disabled={v \|\| !A}` | 只要校验通过即点亮，可直接提交 |

---

## 三、 密码复杂度规则集 (反编译提取自数组 `D`)

注册表单对密码执行以下 5 项前端硬性规则校验，所有规则均满足时标记为合法：
```javascript
const D = [
  { id: "minChars",    label: "At least 8 characters",    test: t => t.length >= 8 },
  { id: "upperCase",   label: "One uppercase letter",     test: t => /[A-Z]/.test(t) },
  { id: "lowerCase",   label: "One lowercase letter",     test: t => /[a-z]/.test(t) },
  { id: "number",      label: "One number",               test: t => /\d/.test(t) },
  { id: "specialChar", label: "One special character",    test: t => /[^A-Za-z0-9]/.test(t) }
];
```

* **推荐生成密码格式**：长度 16 位，包含大写、小写、数字及 `!@#$%^&*`。

---

## 四、 提交控制流与 API 接口契约

### 1. 提交控制函数 (Inertia.js POST)
```javascript
// 按钮解禁条件:
N = (data.password === data.password_confirmation && data.password !== "");
A = emailRegex.test(data.email) && rules.every(r => r.test(data.password)) && N;

// 提交函数:
const onSubmit = (event) => {
  event.preventDefault();
  // 经由 Inertia.js 协议向服务端派发 POST 请求
  post("/register");
};
```

### 2. HTTP 接口契约
* **请求方式**：`POST`
* **请求 URL**：`https://dashboard.plainproxies.com/register`
* **Headers**：
  * `X-Inertia`: `true`
  * `X-XSRF-TOKEN`: `<cookie_xsrf_token>`
  * `Content-Type`: `application/json`
* **Request Payload**：
  ```json
  {
    "email": "test_user@outlook.com",
    "password": "StrongPassword123!",
    "password_confirmation": "StrongPassword123!"
  }
  ```
* **前置弹窗处理**：
  页面首次载入时，若 Cookiebot 弹窗覆盖视口，需预先执行：
  ```javascript
  const allowBtn = document.querySelector('#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll');
  if (allowBtn) allowBtn.click();
  ```

---

## 五、 自动化驱动实现建议

由于 PlainProxies 前端无 Captcha 阻拦，其注册流程属于**最高优先级秒通目标**。
直接实现 Playwright 驱动步骤：
1. 访问 `https://dashboard.plainproxies.com/register`；
2. 检查并点击 Cookiebot 接受按钮；
3. 填入 `email`、`password`、`password_confirmation`；
4. 校验 `Sign up` 按钮解除 `disabled` 状态；
5. 点击提交并监听重定向至 Dashboard；
6. 提取邮件激活或直接自控制台导出试用代理。

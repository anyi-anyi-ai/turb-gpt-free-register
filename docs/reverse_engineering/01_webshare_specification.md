# 逆向工程技术规范：Webshare 代理全自动采集与鉴权链路

> **文档状态**：已完成逆向验证（Verified）  
> **更新时间**：2026-10-08  
> **目标网站**：Webshare (`https://dashboard.webshare.io/register`)  
> **交付标的**：100 个纯净数据中心/住宅代理节点 (Port: 3129) + API Download Token  

---

## 一、 前端技术栈与架构特征

* **应用框架**：Vite + React 18 SPA + Material-UI (MUI v5)
* **网络通信**：Axios 单例封装 (`f$`)，基准 API 地址为 `https://proxy.webshare.io/api`
* **路由机制**：TanStack Router / 客户端 History 模式
* **关键 Chunk**：
  * `dashboard-shell-*.js`：核心 API 接口与全局 Axios 拦截器
  * `register-*.js`：注册表单受控组件与提交状态机
  * `useRecaptcha-*.js`：Google reCAPTCHA v2 / Enterprise 隐式调用适配器

---

## 二、 DOM 元素与受控状态机

| 业务字段 | DOM 选择器 | 属性特征 | React 内部受控状态 | 备注 |
| :--- | :--- | :--- | :--- | :--- |
| **邮箱输入框** | `input[data-testid="email-input"]` | `type="email"`, `id="email-input"` | 状态变量 `y` | 必须为合法邮箱格式 |
| **密码输入框** | `input[data-testid="password-input"]` | `type="password"` | 状态变量 `D` | 建议 ≥ 12 字符，含大小写字母、数字与特殊符号 |
| **服务条款复选框** | `span[data-testid="tos-checkbox"]` | 内嵌 `<input type="checkbox">` | 状态变量 `M` | **关键陷阱：位于 `<form>` 标签外部**，必须触发原生 `click` 与 React `change` 事件 |
| **注册提交按钮** | `button[data-testid="signup-button"]` | `type="submit"` | 触发 `Oe(e)` | 包含文本 `"Sign Up With Email"` |

---

## 三、 提交逻辑与 API 逆向规范

### 1. 表单提交控制流 (反编译还原)
```javascript
async function Oe(event) {
  event.preventDefault();
  // 1. 字段格式前置校验
  let validationErrors = validateSchema({ email: y, password: D }, schema);
  
  // 2. 强校验：协议必须勾选，且字段必须合法
  if (!(validationErrors || !tos_accepted)) {
    // 3. 调用 reCAPTCHA 获取交互 Token (隐式调用)
    let recaptchaToken = await onSubmitWithReCAPTCHA();
    if (!recaptchaToken) {
      reportError("no recaptcha present on register page when signing up");
      return;
    }
    
    // 4. 发送底层注册 POST 请求
    try {
      setLoading(true);
      let response = await axiosClient.post('/register/', {
        email: y,
        password: D,
        tos_accepted: true,
        recaptcha: recaptchaToken
      });
      
      // 5. 存储 Token 并完成重定向
      setToken(response.data.token);
      redirectTo('/dashboard?showQuickStart=true');
    } catch (err) {
      setLoading(false);
      handleError(err);
    }
  }
}
```

### 2. 底层 HTTP 接口契约
* **请求方式**：`POST`
* **请求 URL**：`https://proxy.webshare.io/api/register/`
* **请求 Headers**：
  * `Content-Type`: `application/json`
  * `Accept`: `application/json, text/plain, */*`
  * `Origin`: `https://dashboard.webshare.io`
  * `Referer`: `https://dashboard.webshare.io/register`
* **Request Payload**：
  ```json
  {
    "email": "MichaelAllen9891jO@outlook.com",
    "password": "StrongPassword9876!@#",
    "tos_accepted": true,
    "recaptcha": "03AFcWeA..."
  }
  ```
* **Response 响应状态**：
  * **HTTP 200 / 201**：
    ```json
    {
      "token": "d8a1f8c4e7b0451a...",
      "logged_in_existing_user": false
    }
    ```
  * **HTTP 400**（重复邮箱）：
    ```json
    {
      "detail": "User with this email already exists."
    }
    ```

---

## 四、 人机验证 (CAPTCHA) 机制与穿透策略

1. **类型与参数**：
   * **验证系统**：Google reCAPTCHA v2 / Enterprise
   * **运行尺寸**：`size: "invisible"`（无常驻 Checkbox 框）
   * **Sitekey**：`6LeHZ6UUAAAAAKat_YS--O2tj_by3gv3r_l03j9d`
   * **挑战 Frame URL**：`https://www.google.com/recaptcha/api2/bframe?k=6LeHZ6UU...`
2. **时序与风控特征**：
   * 提交按钮点击后，Google 后台风控评分耗时约为 **10~15 秒**。
   * **穿透核心保障**：检测探针循环等待必须保持在 **25 秒以上**；一旦发现注入 `bframe`，需额外等待其子 DOM（`table`, `.rc-imageselect-desc`, `#recaptcha-verify-button`）完成加载后再送入 YOLOv8 视觉分类器求解。
   * 若触发动态消隐（消隐后自动补图），需设置 2.8 秒平滑消隐等待，避免在动画期间重复点击导致会话失效。

---

## 五、 激活与 100 节点导出规范

1. **邮件激活**：
   * 注册后服务端发送激活邮件至注册邮箱。
   * 监听邮件并提取包含 `https://dashboard.webshare.io/verify-email/...` 或 `token=` 的链接。
   * 在当前同一浏览器会话中访问激活链接，触发会话持久化。
2. **节点导出 API**：
   * **接口地址**：`https://proxy.webshare.io/api/v2/proxy/list/download/{api_token}/-/any/username/direct/-/`
   * **导出格式**：`ip:port:username:password`
   * **清洗规范**：转换为标准 HTTP 代理格式：`http://{username}:{password}@{ip}:{port}`。

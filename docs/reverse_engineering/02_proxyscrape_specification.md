# 逆向工程技术规范：ProxyScrape 7天独享 100 节点采集与鉴权链路

> **文档状态**：已完成逆向验证（Verified）  
> **更新时间**：2026-10-08  
> **目标网站**：ProxyScrape (`https://dashboard.proxyscrape.com/v2/sign-up`)  
> **交付标的**：100 个 7 天独享粘性代理节点 / 10GB 纯净流量  

---

## 一、 前端技术栈与架构特征

* **应用框架**：Next.js 14+ (App/Pages Router) + React 18 + Material-UI (MUI v5)
* **表单管理**：React Hook Form，核心模式采用 **`mode: "onBlur"`**
* **关键 Chunk**：
  * `pages/sign-up-*.js`：注册主页面、Turnstile 回调与 Continue 禁用状态机
  * `challenges.cloudflare.com/turnstile/v0/api.js`：Cloudflare 人机防刷验证组件

---

## 二、 DOM 元素与受控状态机

| 业务字段 | DOM 选择器 | 属性特征 | 对应状态/规则 | 备注 |
| :--- | :--- | :--- | :--- | :--- |
| **邮箱输入框** | `input[type="email"]` | placeholder: `"Email Address"` | `email` 字段 | 填入后必须触发 `blur` 事件结算 |
| **密码输入框** | `#auth-login-v2-password` | `type="password"` | `password` 字段 | 填入后必须触发 `blur` 事件结算 |
| **确认密码输入框** | `#auth-login-v2-confirm-password` | `type="password"` | `confirmPassword` | 必须与密码严格一致且失焦 |
| **服务条款复选框** | `input[type="checkbox"]` | `MuiCheckbox-root` 内层 | 状态变量 `F` | 必须为 `true` 才能解禁提交按钮 |
| **Turnstile 凭证槽** | `input[name="cf-turnstile-response"]` | `type="hidden"` | 状态变量 `G` | 接收 Turnstile 成功令牌 |
| **提交继续按钮** | `button[type="submit"]:has-text("Continue")` | `MuiButton-contained` | 受控属性 `disabled={es}` | 仅当状态满足时变为可点击 |

---

## 三、 提交逻辑与 Continue 按钮解禁状态机

### 1. 按钮禁用核心判定逻辑 (反编译源码提取)
```javascript
// 核心状态声明
[F, setTermsAccepted] = useState();        // 条款勾选状态
[G, setTurnstileToken] = useState("");     // Turnstile Token 字符串
[H, setIsSubmitting] = useState(false);    // 提交中 Loading 状态

// Continue 按钮的 disabled 计算属性:
es = !G || !F || H;
```

* **三大刚性前置条件（缺一不可）**：
  1. `!G === false`（即 `G` 必须有值）：Cloudflare Turnstile 验证通过，下发有效凭据。
  2. `!F === false`（即 `F === true`）：服务条款复选框已被点击勾选。
  3. `H === false`：表单当前不处于网络请求中。

### 2. 校验模式（关键陷阱）
* 表单配置为 **`mode: "onBlur"`**。
* **规避策略**：使用 Playwright 填入邮箱与密码后，不能立即等待按钮，必须对 input 节点广播原生失焦事件：
  ```javascript
  document.querySelectorAll('input').forEach(el => {
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    el.dispatchEvent(new Event('blur', { bubbles: true }));
  });
  ```

---

## 四、 Cloudflare Turnstile 验证机制与自愈穿透

1. **基本参数**：
   * **验证系统**：Cloudflare Turnstile
   * **Sitekey**：`0x4AAAAAAAFWUVCKyusT9T8r`
   * **容器位置**：位于表单下方，初始可能未在视口内。
2. **交互规范**：
   * **视口滚动**：填表完成后，先执行 `window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' })`，将 Turnstile 容器彻底曝光在可视区域。
   * **自愈机制（防止故障红字）**：
     若因 IP 抖动出现 Verification failed 提示，定期调用原生暴露的官方重置接口：
     ```javascript
     if (window.turnstile && typeof window.turnstile.reset === 'function') {
       window.turnstile.reset();
     }
     ```
   * **禁止操作**：严禁注入含有 `postMessage({type: 'turnstile-auto-click'})` 或篡改 `navigator.plugins = [1,2,3]` 的非官方补丁，否则会被 Cloudflare WAF 直接标记并下发死循环失败。

---

## 五、 激活与 100 节点提取流程

1. **提交表单**：
   * 点击点亮后的 `Continue` 按钮，向后端派发带 `cf_turnstile_token` 的注册请求。
2. **邮件激活**：
   * 监听邮件并提取 ProxyScrape 发送的激活 URL。
   * 在当前会话中导航至激活 URL。
3. **节点提取**：
   * 页面跳转控制台：`https://dashboard.proxyscrape.com/datacenter-proxies`。
   * 抓取控制台展示或导出的 100 个 7 天免费试用节点列表（含对应动态凭证）。

# 逆向工程技术规范：RunningHub 算力工作流全自动注册与 Token 同步链路

> **文档状态**：已完成逆向验证（Verified）  
> **更新时间**：2026-10-08  
> **目标网站**：RunningHub (`https://www.runninghub.ai`)  
> **交付标的**：1000 算力币新手礼包 + `Rh-Accesstoken` + RunningHub2API 路由接入  

---

## 一、 前端技术栈与架构特征

* **前端基底**：React + Ant Design (AntD v5 模态对话框 `.ant-modal`)
* **验证码体系**：天爱旋转验证码（Tianai Captcha，滑动旋转图像对正）
* **账号与礼包机制**：
  * 支持海外站邮箱注册与验证码收信
  * 首登填入邀请码（默认 `rh-v1180`）直接解锁赠送 **1000 RH 算力币**
* **会话标头**：`Rh-Accesstoken`（核心鉴权 Header）

---

## 二、 关键阶段状态机与 DOM 选择器

### 阶段 1：首页导航与注册弹窗激活
* **入口 URL**：`https://www.runninghub.ai`
* **登录入口按钮**：`button.login-btn` 或 `button:has-text('Sign In')`
* **切换邮箱登录选项卡**：`.ant-modal` 内文本包含 `"Email Login"` / `"邮箱登录"`
* **切换注册选项卡**：`.ant-modal` 内文本包含 `"SIGN UP"` / `"注册"`

### 阶段 2：邮箱与密码表单录入
* **邮箱输入框**：`.ant-modal input:not([type='password'])`
* **密码与确认密码**：
  * 密码：`.ant-modal input[type='password']` 第 1 个
  * 确认密码：`.ant-modal input[type='password']` 第 2 个
  * **复杂度规范**：长度 8~16 位，纯大小写英文字母与数字（**严禁特殊字符，否则会触发前端输入正则报错**）。
* **推进按钮**：`.ant-modal button:has-text('Next Step')` / `button:has-text('下一步')`

### 阶段 3：天爱旋转验证码 (Tianai Captcha) 处理
* **特征节点**：`.tianai-captcha-slider`, `.captcha-box`, `.captcha-layer`
* **时序与策略**：前端弹出滑块旋转窗，采用视觉对齐角度算法或等待微调滑动放行；验证通过后弹窗自动消隐进入 Step 2。

### 阶段 4：邮箱验证码与邀请礼包码填入
* **验证码获取**：通过 Microsoft Graph API 毫秒级轮询收件箱，发件人包含 `runninghub`，提取 6 位验证码。
* **验证码输入框**：`input[placeholder*='code' i]`
* **邀请码输入框**：`input[placeholder*='invite' i]`（自动填入 `rh-v1180`，绑定算力币礼包）
* **最终提交**：点击提交按钮完成建号。

---

## 三、 会话鉴权与 RunningHub2API 导入契约

1. **Token 捕获**：
   * 监听注册成功后的网络请求响应头 `Rh-Accesstoken`；
   * 或从 LocalStorage 提取 `token` / `userInfo`。
2. **下游网关自动推送**：
   * 接口地址：`http://127.0.0.1:8788/api/accounts/import`
   * 携带字段：`email`, `password`, `token`, `user_id`, `total_coin`
   * 导入成功后即刻接入 ComfyUI 远程工作流调用池。

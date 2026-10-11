# 逆向工程技术规范：UNED 西班牙学籍系统注册与 Tier-2 教育邮箱获取链路

> **文档状态**：已完成逆向验证（Verified）  
> **更新时间**：2026-10-08  
> **目标网站**：UNED SOID (`https://app.uned.es/soid`)  
> **交付标的**：西班牙本校学籍识别码 (UNED Identifier) + `@alumno.uned.es` 高权重学生教育邮箱  

---

## 一、 业务背景与系统架构

* **机构背景**：Universidad Nacional de Educación a Distancia (UNED，西班牙国立远程教育大学)
* **业务价值**：自动生成真实合法学籍身份，获取微软托管的 `@alumno.uned.es` 教育邮箱（用于解锁 GitHub Student Developer Pack、Azure for Students、ChatGPT Edu 优惠等高价值资产）。
* **技术架构**：.NET ASPX / WebForms 传统服务端渲染 + RapidOCR 字符识别 + Microsoft Graph API 邮箱自动握手。

---

## 二、 关键阶段状态机与 DOM 选择器

### 阶段 1：条款勾选与图形验证码破解
* **入口 URL**：`https://app.uned.es/soid`
* **身份类型选择**：`#chkAlumno`（勾选以学生身份建档）
* **数据保护条款**：`#chkAceptarProteccionDatos`（勾选同意条例）
* **图形验证码识别**：
  * 图片节点：`.img-captcha`
  * 识别组件：本地 RapidOCR 模型引擎（`core/uned/captcha_solver.py`）
  * 输入节点：`#txtCodigoCaptcha`
  * 刷新按钮：`#btnRefresh`（若识别置信度不足 5 位字符，点击刷新直至提取高置信度 5 字符）
* **提交发起按钮**：`#btnIniciarProceso`

### 阶段 2：底座邮箱录入与邮件激活
* **流转 URL**：页面自动跳转至包含 `email-registro` 路径的表单。
* **邮箱输入框**：`#txtEmail` 与 `#txtRepetirEmail`（填入由第一阶段领取的真实 Outlook 邮箱）
* **邮件验证**：
  * UNED 向录入邮箱发送验证邮件，发件人为 `soid@csi.uned.es`；
  * 本地程序经由 Microsoft Graph API 静默轮询并提取激活确认链接；
  * 在当前会话中导航至激活链接完成邮箱验证。

### 阶段 3：西语人设与护照/身份号生成
* **身份伪装库**：`core/uned/identities.py`
* **证件类型**：选择护照（`Pasaporte`）或外国人居留号（`NIE`）
* **表单填入**：
  * 证件号：`#txtDocumento`
  * 名字：`#txtNombre`
  * 姓氏 1：`#txtApellido1`
  * 姓氏 2：`#txtApellido2`
  * 手机号码：`#txtTelefono`（生成合规国际格式）
* **密码设置**：`#txtPassword`（遵循大写字母、小写字母、数字及特殊符号规则，如 `Uned@1234!Zhang#88`）

### 阶段 4：学籍识别码生成与数据持久化
* 注册成功后，服务端下发学生的**唯一学籍标识符（Identificador de usuario / UNED ID）**；
* 根据学籍标识符生成对应的教育邮箱：`{identifier}@alumno.uned.es`；
* 自动落盘至 `data/uned_accounts.json` 与 `turb.sqlite3` 数据库的 `uned_accounts` 资产表。

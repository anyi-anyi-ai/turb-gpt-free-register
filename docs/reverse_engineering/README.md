# 逆向工程工作台与全平台注册规范索引 (Reverse Engineering Workbench)

> **版本**：v1.0.0  
> **建立时间**：2026-10-08  
> **定位**：全量网站注册流程、状态机、人机交互、API 契约的标准化技术规范库，确保所有自动化流程具备可追溯性与二次开发复用性。

---

## 一、 分批次推进路线图 (Batch Roadmap)

### 🟢 第一批次：代理商流量供给侧（已完成深度逆向与技术文档规范）
* [x] **Webshare**：[Webshare 技术规范文档](./01_webshare_specification.md)
  * Google reCAPTCHA (Invisible) + React 外部受控复选框 + `/api/register/` 协议
* [x] **ProxyScrape**：[ProxyScrape 技术规范文档](./02_proxyscrape_specification.md)
  * Cloudflare Turnstile (`0x4AA...`) + `mode: "onBlur"` 校验 + `es = !G || !F || H` 状态机
* [x] **PlainProxies**：[PlainProxies 技术规范文档](./03_plainproxies_specification.md)
  * Inertia.js 协议 + 5项密码强校验规则 + 无 Captcha 秒通通道

---

### 🟢 第二批次：核心大模型与代码智能体平台（已完成深度逆向与技术文档规范）
* [x] **ChatGPT / OpenAI**：[ChatGPT 技术规范文档](./04_chatgpt_specification.md)
  * Auth.js / NextAuth 会话流、Arkose FunCaptcha + PoW 探针逆向、6位邮箱验证码捕获、Codex OAuth Refresh Token 提取
* [x] **xAI Grok**：[Grok 技术规范文档](./05_grok_specification.md)
  * Cloudflare Turnstile 绕过、邮箱分步验证码流、SSO Cookie 提取、sub2api 令牌自动同步
* [x] **GitHub**：[GitHub 技术规范文档](./06_github_specification.md)
  * DataDome 前置拦截规避、Arkose FunCaptcha (Public Key `747B83EC...`) 破解、8位邮件 Launch Code 提取与 PAT 自动签发

---

### 🟢 第三批次：账号底座与学籍资产平台（已完成深度逆向与技术文档规范）
* [x] **Microsoft Outlook / Live**：[Outlook 技术规范文档](./07_outlook_specification.md)
  * SFT 动态流令牌防重放、Arkose FunCaptcha (Public Key `B7D8911C...`) 绕过、PerimeterX 拟人微颤按压、Graph API 邮箱静默免登授权抽取
* [x] **UNED (西班牙学籍)**：[UNED 技术规范文档](./08_uned_specification.md)
  * SOID 注册协议、RapidOCR 动态字符验证码破解、西班牙身份人设生成、Tier-2 `@alumno.uned.es` 高权重学生邮箱资产生成
* [x] **Kiro (AWS Builder ID)**：[Kiro 技术规范文档](./09_kiro_specification.md)
  * **100% 纯 HTTP 协议流实现（无浏览器消耗）**、TLS 1.3 `chrome131` 模拟、RFC 8628 OAuth Device Code 授权、长效 Access/Refresh Token 自动化交换与落盘

---

### 🟢 第四批次：多模态 AI 影音创意平台（已完成深度逆向与技术文档规范）
* [x] **RunningHub**：[RunningHub 技术规范文档](./10_runninghub_specification.md)
  * AntD 弹窗状态机、天爱旋转滑块验证码绕过、1000 算力币新手礼包码激活、`Rh-Accesstoken` 提取与 RunningHub2API 导入
* [x] **Suno AI**：[Suno 技术规范文档](./11_suno_specification.md)
  * Cloudflare Turnstile 前置穿透、Clerk Auth 架构、Microsoft OAuth 联合登录、Clerk Session 与每日 50 Credits 循环提取
* [x] **ElevenLabs**：[ElevenLabs 技术规范文档](./12_elevenlabs_specification.md)
  * Firebase Auth 邮件激活链接（`oobCode`）抽取、Arkose / hCaptcha 人机拼图协同、10,000 字符 TTS 配额与 API Key 导出
* [x] **Udio / Stable Audio / Muse**：[Udio / Stable Audio / Muse 联合技术规范文档](./13_udio_stableaudio_muse_specification.md)
  * Udio (Supabase Magic Link + 随机档案创建)、Stable Audio (**Auth0 免验 OTP 秒建号 + 50 点免费音频额度**)、Muse (学生邮箱会话绑定与 Cookie 提取)

---

## 二、 文档技术规范标准 (Standard Specification Template)

后续每个批次进入逆向工作台分析后，均按以下统一模板沉淀规范文件：
1. **前端技术栈与架构特征**：识别框架（React/Vue/Next/Inertia）、关键打包 Chunk、全局单例网络库；
2. **DOM 元素与受控状态机**：整理真实选择器、父子嵌套结构、内部受控变量名、失焦（Blur）要求；
3. **提交控制流与 API 契约**：还原底层提交函数，明确 URL、Method、Headers、Payload 格式及成功/去重响应；
4. **人机验证 (CAPTCHA) 机制与穿透策略**：指明 Captcha 类型、Sitekey、渲染时序及针对性绕过或自愈算法；
5. **资产提取与持久化**：标明 Token、Cookies、节点、API Key 的抓取方式与标准化清洗落盘方案。

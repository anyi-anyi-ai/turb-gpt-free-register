# Original User Request

## 2026-09-29T00:58:04Z

全自动完成所有剩余 UNED 西班牙学生邮箱（@alumno.uned.es）激活及 Office 365 自动转发至绑定个人 Outlook 邮箱并保留邮件副本的全量交付任务。

Working directory: f:/github/turb-gpt-free-register
Integrity mode: development

## Requirements

### R1. 全量扫描与待处理队列加载
从本地数据库 `turb.sqlite3` 中查询所有 `forwarding_enabled = 0` 的待处理 UNED 账号（当前剩余 52 个账号），提取对应学生标识符、密码、绑定个人 Outlook 邮箱及身份信息。

### R2. 三阶段全自动管线执行
对队列中的每一个 UNED 账号执行标准 3 阶段流水线：
1. **阶段 0（初始凭据保障）**：若账号未在 UNED 落盘密码，通过 Microsoft Graph API / REST API 从个人 Outlook 邮箱提取激活链接并秒级完成密码设置。
2. **阶段 1（学生邮箱激活）**：通过 Playwright 登录 UNED SSO 门户，直达 `serviwebng.uned.es/correo/activar-correo.asp` 提交激活，确认获得开通响应（如 `20951: Cuenta de correo ya activada`）。
3. **阶段 2（Office 365 转发配置）**：通过 `login_hint` 参数直连微软联合认证，登录 Office 365 Outlook Web，打开设置 -> 转发面板，配置转发到绑定的个人 Outlook 邮箱，严格勾选“保留转发邮件的副本 (Keep a copy of forwarded messages)”并保存。

### R3. 网络代理路由与异常自愈
统一使用本地 Docker Resin 稳定欧区高可用节点（如荷兰 `NL` 出度代理 `http://NL:resin_proxy_c3d7b8e1f0a2496582d9183b@127.0.0.1:2260`），拦截 Google Fonts 与统计脚本确保毫秒级响应；若遇到临时锁定（`Cuenta Bloqueada`）或网络抖动，自动记录并在稍后轮询重试。

### R4. 数据状态落盘与 Obsidian 资产同步
每个账号处理完成后，实时将 SQLite 中该账号状态更新为 `forwarding_enabled = 1, status = 'activated'`，并同步生成/更新 Obsidian 独立档案笔记及 J:\obsidian库\ThinkDoKit-Full-1.2.0\邮箱\学校邮箱\UNED学校邮箱汇总表.md。

## Acceptance Criteria

### 邮箱激活与转发验证
- [ ] 数据库 `turb.sqlite3` 中所有 92 个 UNED 账号的 `forwarding_enabled` 字段达到 100%（或剔除永久失效账号后的全部有效账号达到 1）。
- [ ] 每个已处理账号均已通过 Office 365 成功设置邮件转发至其绑定的专属个人 Outlook 邮箱。
- [ ] 每个账号在 Office 365 中均勾选了“保留转发邮件的副本”选项。

### 系统数据与知识库一致性
- [ ] 本地 SQLite `turb.sqlite3` 数据表 `uned_accounts` 状态全部更新完毕。
- [ ] Obsidian 路径下的汇总笔记 J:\obsidian库\ThinkDoKit-Full-1.2.0\邮箱\学校邮箱\UNED学校邮箱汇总表.md 状态刷新为最新已激活/已转发状态。

# 技术演进路线图（Roadmap & Language Decision）

> 日期：2026-10-07。依据：2026-10-07 审计（spec/plan 见同目录）+ 同类项目与生态调研。
> 结论先行：**不做全量语言重写**；Python 仍是主语言；架构塑造分三步走（整改 → 流程抽象 → 面板合并/多机化）；技术栈做"有证据的增量更新"，Go 仅以独立 sidecar 形式按需引入。

## 一、生态调研结论

### 同类项目对标

| 项目 | 定位 | 可借鉴点 |
|---|---|---|
| [AaronL725/grok-register](https://github.com/AaronL725/grok-register)（2.3k★，Python，30+ 模块） | 单平台（Grok）批量注册，WebUI/GUI/CLI 三入口，DrissionPage 驱动，sing-box 多协议代理（VLESS/Hysteria2），grok2api 直接入池，SSO 风控筛查 | ① 单平台项目 30 个模块就够——本项项目 15+ 平台 950 文件，印证"平台即插件"的必要性；② sing-box 协议代理层；③ 三入口共享同一 schema 的做法 |
| [Wei-Shaw/sub2api](https://github.com/Wei-Shaw/sub2api)（~43k★） | 订阅统一转 API 中转 | 本项目的下游。其接口约定（OpenAI 兼容）应作为导出格式的稳定契约 |
| new-api / one-api | API 统一管控 | 多池聚合管理的成熟参照；本项目导出的账号最终汇入此类平台 |

### 反检测浏览器栈（2026 横评结论）

- **Patchright**（Playwright 补丁版）：生态兼容最好，本项目主力，维持。
- **Nodriver**：不走 CDP，2026 年某 31 个真实 Cloudflare 目标的基准中是唯一全过的工具——项目已有 POC（`run_github_nodriver_poc.py`），应正式化为"硬目标"备胎。
- **Camoufox**（Firefox ESR 基底）：开源反指纹天花板，与全 Chromium 指纹池天然错开——适合最敏感的流程（如 Outlook/微软风控）做第二指纹池。
- **DrissionPage**：国产生态、requests+浏览器混合模式开发体验好（同类项目在用），但引入第四种范式会加剧本项目的"多栈漂移"病灶——**仅评估，不引入**。

### 语言重写的社区共识（Reddit/HN/工程博客）

- I/O 密集（等页面、等代理、等验证码）→ 重写几乎无收益；CPU 密集解析 → Go 约 5-8x，Rust 约 10-15x。
- 共识模式：**Python 保留脆弱易变的业务逻辑，只为测得的热点写第二语言模块**（strangler fig）。
- 反例警示：抓取/注册类项目的 selectors 与风控规则每周都在变——迭代速度就是产能，这正是 Python 的主场。

## 二、方向一：技术更新与扩展（采纳/评估/观望清单）

**立即采纳（低风险，高确定性）**

1. **nodriver 正式化**：把 POC 升级为 `core/browser/` 下与 patchright 平级的第二个引擎，仅在目标被 Cloudflare/PerimeterX 硬拦时按节点开关切换——配合现有 `node_quarantine` 形成自动降级。
2. **Camoufox 第二指纹池**：仅对微软系（Outlook/Copilot）流程试点；BitBrowser/CloakBrowser 继续承担量产。
3. **sing-box 代理协议层**：住宅池之外补 VLESS/Hysteria2 出口（同类项目验证过的组合），与现有 Clash（9097 API）并存，`common/proxy_switch.py` 增加 `singbox` 出口模式。
4. **结构化日志与指标**：审计确认日志是散装 print + 局部脱敏。引入 structlog（JSON 行）+ Prometheus client（每 worker 暴露：注册成功数/失败分类/打码耗时/代理消耗），Grafana 看板。这是后续一切容量决策的数据基础。
5. **打码本地化**：vision_solver 已有多模型投票；把 OpenRouter 调用换成"本地小 VLM（Qwen2.5-VL-7B 级，vLLM/Ollama）优先 + 云端兜底"，按当前第三方打码单价估算可省 60-80% 成本。
6. **云端容器化**：server.py / k12 / 各 2api 同步目标统一 docker compose；Windows 侧因 BitBrowser/CloakBrowser 是 GUI 程序保持裸机。

**评估（半年内做试点再定）**

7. **Postgres 迁移**：触发条件 = 出现第二个常驻写库进程或需要多机共享账号池。在此之前 P2 计划的原子化 + WAL 已够用；不预先迁移。
8. **任务队列**（NATS/Redis）：触发条件 = 多机 worker。单机维持 SQLite jobs 表 + 进程内线程池（现有 registration_service 模式）。

**观望**

9. DrissionPage、browser-use/Stagehand 类 Agent 框架（本项目已有 browser_use 集成，够用）、MCP 化内部工具。

## 三、方向二：代码架构塑造（三步走）

**第一步（已就绪，立即执行）＝ 整改计划 P0-P3**
`plans/2026-10-07-remediation-plan.md` 的 20 个任务。死代码不清掉，任何架构动作都在流沙上做。

**第二步：流程抽象（整改完成后 2-4 周）**

审计显示 6 个音乐平台 service 与 3 套 ChatGPT 浏览器驱动是同一流水线的复制体。抽象为：

```
BaseRegistrationFlow（模板方法，core/flows/base.py）
  acquire_email() → open_session() → submit_identity()
  → solve_captcha() → wait_otp() → complete_profile()
  → capture_session() → persist() → export() → push_downstream()
Driver 接口（core/drivers/contract.py）：goto/click/fill/screenshot/solve
  ├─ PatchrightDriver（主力）
  ├─ NodriverDriver（硬目标）
  ├─ CamoufoxDriver（微软系试点）
  └─ 指纹浏览器 Driver（Bit/Cloak/Roxy 走现有适配器）
平台 = core/platforms/<name>/（flow 子类 + 平台特有步骤 + 声明式 manifest）
```

判据：新增一个平台应当是"一个 flow 子类 + 一份 manifest"，而不是再抄 3000 行。抽象顺序：先收编 6 个音乐 service（最同质），再收编 3 套 ChatGPT 驱动流（`codex_oauth` 三兄弟的公共工具函数随 Driver 接口下沉）。

**第三步：面板合并与多机化（按需）**

- **面板合并**：FastAPI 胜出（async、SSE、pydantic schema）。Flask app.py 的约 200 路由按域拆成 5-6 个 router 渐进搬迁，统一 auth 中间件（P0 已修的 session 机制直接复用）。迁移期挂反代按前缀分流，Flask 全迁完即退役。
- **多机化**（触发条件：单机浏览器 worker ≥ 30 或需要异地 IP 池）：控制面（API+Postgres+队列）与执行面（browser worker，Windows 裸机/独立 VM）分离；worker 只实现 Driver 接口 + 上报，不碰 DB。Go sidecar（见下）此时顺势作为 worker 内的协议引擎。
- **编排器统一**：run_full_flow 的 subprocess 编排保留（浏览器进程隔离是优点，崩溃不连带），但收编为唯一的 job runner，子入口全部注册进 webui/scripts.py schema（消灭"绕过面板的隐藏入口"）。

## 四、方向三：语言选型（明确结论）

**不做全量重写。Python 保留为主语言。**

理由（按权重）：

1. **负载特征不匹配重写收益**：本项目时间都耗在页面等待、代理往返、打码响应上（I/O 密集），Go/Rust 的 5-15x 优势只在 CPU 密集解析；把 16.8 万行业务知识（selectors、时序、错误分类正则、风控对抗经验）迁移到类型系统里要人年，迁移期内目标站一改版，Python 侧的修补经验全部作废。
2. **生态单边性**：这个赛道的工具链事实上一边倒地是 Python——patchright / nodriver / Camoufox / DrissionPage / curl_cffi / faster-whisper 全是 Python 优先；Go 侧（rod、chromedp）没有可用的反检测生态，Rust 侧（chromiumoxide）更薄；下游 sub2api/grok2api 系也全是 Python。重写等于离开主战场。
3. **审计已证明问题不在语言**：本项目的一切痛点（复制粘贴扩展、死代码、跨进程并发、明文凭据）都是工程纪律问题，Go 重写只会把它们忠实地翻译成 Go 版。
4. **同类项目用脚投票**：2.3k★ 的 grok-register 是 Python 3.9+；sub2api 生态是 Python；没有任何同类头部项目选择 Go/Rust 做全量。

**但以下三个 Go/Rust 楔子值得列入观察清单，触发条件明确后才动工（strangler fig，每个都是独立 sidecar/小工具，不动主仓库语言）：**

| 楔子 | 形态 | 触发条件 | 预期收益 |
|---|---|---|---|
| Go 协议引擎（bogdanfinn/tls-client + stealth） | localhost HTTP sidecar，Python 调用 | 协议流 RPS 需求 > 50/s，或 curl_cffi 的 JA3 库追不上目标（被 TLS 层硬拦） | TLS 指纹池并发轮换、单二进制部署进 worker |
| Go/Rust 代理流量转发器 | 每 worker 常驻进程 | 住宅代理按流量计费成为最大成本项（traffic_saver 现为 Python 过滤，热路径字节泵） | 省流量热路径 10x+，CPU 让给浏览器 |
| Rust 账号池查询服务 | 独立只读服务 | 多机化后 Postgres + 高频查池 | 不急，Postgres 迁移后再说 |

**触发条件不复核不出手**：设季度检查点，用第二步埋的 Prometheus 指标验证——瓶颈若真是语言（CPU 饱和、GC 停顿、TLS 被拦）再动，否则不动。

## 五、里程碑总览

| 时间 | 里程碑 | 验收 |
|---|---|---|
| 第 1-2 周 | 整改 P0-P3（20 任务，计划已就绪） | 测试全绿，根目录 ≤55 文件，无高危漏洞残留 |
| 第 3-4 周 | P2 数据层深化 + structlog/Prometheus 埋点 | 看板可见成功率/失败分类/打码耗时 |
| 第 5-8 周 | BaseRegistrationFlow + Driver 接口收编 6 个音乐 service | 新平台接入 ≤ 1 个 flow 子类 + 1 份 manifest |
| 第 9-12 周 | nodriver 正式化 + Camoufox 微软系试点 + sing-box | 硬目标节点自动降级，微软系成功率对比数据 |
| 第 13 周+ | 面板合并（FastAPI 单面板）；按季度检查点评估 Go 楔子 | Flask 退役；触发条件有数据支撑 |

## 附：参考资料

- [Patchright、Nodriver、Camoufox：2026 三大反检测框架横评（CSDN）](https://damodev.csdn.net)
- [Go vs Python for Web Scraping（Crawlbase）](https://crawlbase.com) / [Olostep 语言选型](https://www.olostep.com) / [Rust vs Go vs Python 速度对比](https://www.iwebscraping.com)
- [go-rod/rod](https://github.com/go-rod/rod) / [bogdanfinn/tls-client](https://pkg.go.dev/github.com/bogdanfinn/tls-client)
- [AaronL725/grok-register](https://github.com/AaronL725/grok-register) / [Wei-Shaw/sub2api](https://github.com/Wei-Shaw/sub2api)

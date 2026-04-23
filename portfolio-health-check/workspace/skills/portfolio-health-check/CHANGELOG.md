# Portfolio Health Check — 变更说明

> 更新日期: 2026-04-23

---

## [2026-04-23] 异步任务错误处理收紧

### 健壮性

- `call_remote_phase_api.py` 的异步 submit 现在只在任务端点**确实不可用**时回退同步：
  - `404 / 405 / 501` 视为服务端尚未部署 `/phc-tasks`
  - submit 阶段的网络错误（`URLError`）仍允许回退到原同步调用
  - 不再对任意 `HTTPError` / 任意异常一律静默回退，避免掩盖真实的 4xx/5xx 问题
- 异步 `poll` 路径与同步路径统一错误呈现：
  - 非 404 的 `HTTPError` 统一走 `_format_api_error`
  - 连接错误统一输出 `remote task poll connection error`
- 异步 PDF 结果新增校验：
  - `pdf_base64` 缺失时报错
  - 非法 base64 或解码后不是 PDF 头的结果会直接拒绝
- `_submit_task` / `_poll_task` 返回非法 JSON 或非对象 JSON 时，改为给出明确 `SystemExit`，不再抛原始解析异常

### 测试

- `test_call_remote_phase_api.py` 新增异步错误分支覆盖：
  - submit `404` 回退同步
  - submit `400` 不回退而直接报错
  - poll `HTTPError` 人话格式化
  - async PDF 非法 `pdf_base64` 拒绝

---

## [2026-04-22] call_remote_phase_api 异步任务模式

### 变更

- `call_api()` 内部切换为 submit + poll 异步模式：
  - 先 POST `/phc-tasks` 提交任务，拿到 `task_id`
  - 每 10 秒 GET `/phc-tasks/{task_id}` 轮询状态
  - 如果服务端不支持 `/phc-tasks` 端点，自动回退到原同步调用
- `download_pdf()` 同样改为异步模式，completed 后从 `result.pdf_base64` 解码 PDF
- 对外接口签名完全不变，上层 skill 代码零改动

### 为什么改

1. **超时问题**：Phase-2 诊断 11 只股票需要 20-30 分钟，长连接容易断
2. **堆积问题**：服务端并发上限 5，第 6 个请求阻塞在 HTTP 线程，客户端看不到队列状态

### 兼容性

- 自动检测：如果 `/phc-tasks` 端点不存在 / 方法不允许 / 未实现，或 submit 阶段出现网络错误，走原来的同步路径
- 无需修改 skill 调用代码

---
## [2026-04-22] PR review 二轮修复：SSRF DNS rebinding + 代码整理

### 安全

- **`qveris_client.py` — SSRF DNS rebinding 二次校验（🔴 必做）**：
  - `_is_safe_full_content_url` 原实现只在 URL parse 阶段校验 hostname：域名走 `ipaddress.ip_address()` → `ValueError` → 直接 `return True`，完全跳过 IP 检查
  - 攻击场景：QVeris 上游被入侵后，`full_content_file_url` 指向 TTL=0 的恶意域名，首次 DNS 解析返回公网 IP 通过校验，`urlopen` 实际连接时重新解析到 `127.0.0.1`，探测内网
  - 修复：新增 `_is_safe_resolved()` 通过 `socket.getaddrinfo()` 做 DNS 解析，拒绝解析到 private / loopback / reserved / link-local / multicast / unspecified 的地址
  - `_is_safe_full_content_url` 中域名分支改为调用 `_is_safe_resolved()`，不再直接放行

### 代码整理

- **`call_remote_phase_api.py` — 消除 `count_holding_codes` 重复调用（🟡 建议）**：
  - `_compute_timeout` 返回值从 `int` 改为 `tuple[int, int]`（timeout + stock_count）
  - `_prepare_request` 直接解包元组，不再二次调用 `count_holding_codes(payload)`
- **`qveris_client.py` — 移除不可达 `IndexError` catch（🟡 建议）**：
  - quote CLI 的 `except (ValueError, IndexError)` 改为 `except ValueError`
  - `len(parts) > 1` 已保护了 `parts[1]` 访问，`IndexError` 永远不会触发

### 测试

- `test_call_remote_phase_api.py`：5 个 `ComputeTimeoutTests` 更新为验证 `(timeout, stock_count)` 元组
- `test_qveris_client.py`：2 个已有测试补 mock `_is_safe_resolved`；新增 `test_rejects_dns_rebinding_to_loopback`
- 全量 46/46 通过

---

## [2026-04-21] Address PR #20 review feedback

### 安全与健壮性

- `qveris_client.py` 为 `full_content_file_url` 增加安全校验：
  - 仅允许 `http` / `https`
  - 拒绝缺失 host、带账号信息、`localhost`、`.local` 以及 loopback / private / link-local / reserved IP
- `truncated_content` 回退解析失败时补充 stderr 日志，避免静默吞错
- `call_remote_phase_api.py` 在两次 `URLError` 都失败时保留第一次和第二次原因，便于排查网络问题
- `_read_pdf` 非 PDF 报错现在截断详情到 500 字符，避免过长输出

### 代码整理

- 调整 `_format_api_error` 判定顺序，优先识别 Python / FastAPI error envelope，降低未来 schema 演进时的误判风险
- 抽取 `call_api` / `download_pdf` 共用的请求准备逻辑，统一 timeout 日志与幂等请求构造
- `count_holding_codes` 改为基于 `.get()` 的安全嵌套访问，减少异常分支

### 文档与测试

- 同步 `docs.portfolio-health-check-api.md` 到 Java 网关版本：
  - 更新 `X-API-Key` / `tenant-id` 鉴权说明
  - 更新 `/admin-api/aireport2/portfolio-health/...` 端点路径
  - 更新动态超时与重试行为
  - 删除已过时的 usage report 说明
- 删除重复的 `[2026-04-15] 隐私边界、状态清理与批量提问优化` 条目
- 单测补充覆盖：
  - `_format_api_error` 新判定顺序
  - `_send_with_retry` 双失败报错内容
  - `_read_pdf` 非 PDF 详情截断
  - `qveris_client.py` 的 URL 安全校验与 fallback 日志

---

## [2026-04-20] 适配 ReportServer Java 网关 + 客户端超时/重试重写 + skill 文案更新

### `call_remote_phase_api.py` 重写

- **认证**:
  - URL 硬编码到 `https://admin.deepseekdata.com`(蓓曦星途平台)。`PORTFOLIO_API_BASE_URL` 可覆盖,默认即可用
  - 放弃 `Authorization: Bearer`,改为发 **`X-API-Key`**(Java 网关 `ApiKeyAuthInterceptor` 读的字段)
  - 新增 `tenant-id` 头(Yudao 多租户守卫要求),默认 `"1"`,`PORTFOLIO_API_TENANT_ID` 可覆盖
  - Env:`PORTFOLIO_API_KEY` + `PORTFOLIO_API_TENANT_ID`(旧 `PORTFOLIO_API_TOKEN` 废弃)
- **路径**:硬编码前缀 `/admin-api/aireport2/portfolio-health`(走 Java 网关),不支持直连 Python 模式
- **幂等**:每次调用生成 UUID `X-Idempotency-Key`,retry 时复用同一个 key,配合 Java 侧 `openapi_consume_record` 幂等表避免重复扣分
- **超时**:从"不设超时"改为动态计算 `max(stock_count × 3min, 30min)`:小 payload 30min 保底(LLM 舆情 + PDF 渲染固定开销大),大 payload 线性扩
- **重试**:网络层错误(`URLError`)自动重试 1 次,同一幂等 key 命中 Java 缓存不重算不重扣
- **错误解析**:统一兼容 Java(Yudao)`{"code","msg"}`、Python 原始 `{"status","error_message"}`、FastAPI 包裹 `{"detail":{...}}` 三种错误 envelope,给用户人话

### Skill 文案

- `SKILL.md` + `portfolio-deep-diagnosis/SKILL.md` 的"第 2 步"现在必须按 `max(stock_count × 3, 30) 分钟` 主动向用户朗读预计等待时间
- 错误处理章节补充"开放平台积分不足"等 Java 错误对应话术

### 测试

- `tests/test_call_remote_phase_api.py` 38 项单元测试全绿,覆盖:
  - URL 路由 / UUID 幂等 key / tenant-id 头 / X-API-Key 头
  - timeout 下限 + 线性扩 / retry 复用同 key
  - 三种错误 envelope 解析
  - PDF byte content-type 检测 / 非 PDF 降级

### 配套服务端改动(在 `BeixiHub/portfolio-health-check-skill-` 分支 `feat/concurrency-sentiment-cache-20260420`)

- 服务端并发限流 `MAX_CONCURRENT_COMPUTE=5`
- 舆情双层缓存(L1 文章 + L2 LLM 结果)
- `LLM_CONCURRENCY` 8 → 30
- nginx `proxy_read_timeout` 1800s → 7200s

### 压测(15 并发 phase-2)

| 指标 | 有缓存前 | 有缓存后 |
|---|---|---|
| 成功 PDF | 10/15 | 13/15 |
| 504 超时 | 3/15 | 0/15 |
| 最快单次 | 332s | 304s |

---

## [2026-04-16] QVeris 能力增强、Phase 2 长耗时适配与文档收敛

### QVeris 客户端增强

- `qveris_client.py` 的 `lookup/identify` 现在优先走直接识别链路：
  - 自动判断输入是全代码、裸代码还是名称
  - 直接调用代码转换、公司基本信息和行业识别工具
  - 只有直接识别失败时才回退到搜索驱动模式
- 新增 `quote` CLI 子命令：
  - 支持查询实时价格
  - 支持按 `NAME=CODE:SHARES` 输入计算持仓市值和仓位占比
- THS 数据采集链路增强：
  - 已知工具 ID 直接执行，不再依赖先 search 再 execute
  - `full_content_file_url` 下载失败时会重试，并回退到 `truncated_content`
  - 如果返回结果中混有多个证券代码，会先按 code 拆分序列，再做汇总和导出

### Phase 2 / PDF 长耗时适配

- `call_remote_phase_api.py` 的主请求不再设置客户端超时
- `phase2` / `phase2_pdf` 在持仓股票较多时，可以持续等待服务端完成计算和渲染
- 保留 `/api/v1/usage/report` 的 best-effort 10 秒超时，不阻塞主流程

### Skill 与文档说明更新

- `SKILL.md` 和 `portfolio-deep-diagnosis/SKILL.md` 明确补充：
  - Phase 2 是长耗时步骤
  - 股票较多时等待 10 分钟甚至更久都可能是正常情况
  - 需要在执行前主动向用户说明会持续等待结果返回
- 删除重复文档 `docs.call-remote-phase-api.md`
- 将说明统一收敛到主工作流文档和 `docs.portfolio-health-check-api.md`

---

## [2026-04-15] 新增持仓股票代码计数与用量上报

### 功能变更

- `call_remote_phase_api.py` 新增 `count_holding_codes(payload)` 函数，统计 `holdings` 中含 `code` 字段的持仓数量
- 新增 `report_usage(base_url, token, phase, stock_count)` 函数，在每次 API 成功调用后将 `phase` 和 `stock_count` POST 到 `/api/v1/usage/report`
- `call_api` 和 `download_pdf` 在成功返回后自动触发用量上报
- 上报为 best-effort 模式（超时 10 秒，任何异常静默忽略），不阻塞主流程

### 影响范围

- 所有经过 `call_api` / `download_pdf` 的 Phase 2、Phase 2 PDF、Phase 3 调用均会触发上报
- 需要服务端部署 `POST /api/v1/usage/report` 端点来接收计费数据；端点未就绪时客户端不受影响

### 文档

- 更新 `docs.call-remote-phase-api.md`：执行流程新增步骤 6（用量上报）、核心函数新增 2 个函数说明、注意事项新增第 6 条

### 测试

- 新增 7 个测试用例（`CountHoldingCodesTests` × 3 + `ReportUsageTests` × 4），连同已有用例，全部 11 个测试通过

---

## [2026-04-15] 隐私边界、状态清理与批量提问优化

### 隐私与记忆策略

- 在 `workspace/AGENTS.md` 中新增 `Portfolio Privacy Rule`
- 明确要求在开始组合诊断前，先向用户说明：
  - 出于隐私保护考虑，持仓、仓位、风险偏好等信息默认只用于本次分析
  - 默认**不写入** `memory/*.md` 或 `MEMORY.md`
- 明确只有在用户显式要求“记住”时，才允许最小化保存偏好或结论
- 明确禁止默认保存持仓细节、风险参数、账户规模、Phase 2/3 结果等敏感信息

### `state/` 临时文件治理

- 明确将 `state/` 下文件定义为**当前咨询流程的临时状态文件，不属于 memory**
- 统一将以下文件纳入结束咨询时的清理范围：
  - `state/phase2_payload.json`
  - `state/phase2_report.pdf`
  - `state/phase2_result.json`
  - `state/phase3_payload.json`
  - `state/phase3_result.json`
- 新增规则：结束咨询时，先提醒用户这些临时文件会被删除，然后直接清理
- 不再要求对 `state/` 清理做二次确认

### 主技能交互改造

- 在主 `SKILL.md` 中新增“合规边界”：
  - 明确这是研究、诊断和配置层面的信息整理
  - 不承诺收益，不给确定性的买卖、下单、仓位指令
- 更新开场模板，加入隐私前置告知
- 将 Phase 2 的 4 个参数收集改为**一次性问完**
  - 用户可用 `3-3-2-2` 等格式集中回复
  - 若用户只回答一部分，也要求一次性补问剩余问题
- 将 Phase 3 的约束收集也改为**一次性问完**
  - 支持类似 `1,2 / 1,2 / 2 / 1` 的集中回复格式
- 在阶段结束处新增结束咨询提示：
  - 若用户确认结束，会清理本次分析生成的 `state/` 临时文件
- 在 `workspace/AGENTS.md` 中新增 client 对话规则：
  - 任何时候对 client 开口，先用 `收到`、`好的`、`明白了` 等简短确认起手
- 将 Phase 2 的等待提示更新为：
  - 明确告知用户深度诊断预计耗时约 4 分钟

---

## 架构变更：客户端/服务端分离

### 之前（旧版）

客户端内嵌完整计算代码（`scripts/` 目录），包含：

- `compute/` — 15 个量化计算模块（returns, correlation, risk_metrics, concentration, factor_engine, liquidity 等）
- `prescribe/` — 优化处方引擎（inference, mapping, rescore, stress, strategy_templates 等）
- `diagnosis.py` / `pipeline_main.py` — Phase 2 诊断编排
- `prescription.py` / `prescription_main.py` — Phase 3 优化编排
- `data_loader.py` / `date_utils.py` / `qveris_client.py` — 数据加载与 QVeris 交互
- `generate_report_html.py` / `html_pdf.py` — HTML/PDF 报告生成
- `tests/` — 30+ 个单元测试
- 总计 60+ 个 Python 文件

**问题**：客户端体积臃肿，依赖 pandas/numpy 等重型库，每次更新计算逻辑需要重新部署客户端。

### 现在（新版）

计算代码全部封装到远端 API 服务器，客户端仅保留：

- `call_remote_phase_api.py` — HTTP 桥接脚本，负责将 JSON payload POST 到远端 API
- 三个 API 端点：`phase2`（JSON 诊断）、`phase2_pdf`（PDF 报告）、`phase3`（优化处方）

**好处**：客户端零依赖（仅 stdlib），计算逻辑独立迭代，skill 只关注交互和参数收集。

---

## Skill 逻辑优化

### SKILL.md（总控）

- **精简**：从 125 行缩减到 ~60 行，去除冗余规则描述
- **参数表格化**：Phase 2 的 4+1 个参数、Phase 3 的 6 个约束全部用表格列出，明确 JSON 字段名和可选值
- **PDF 策略变更**：默认调用 `phase2_pdf` 生成 PDF 报告；仅在 PDF 接口失败时降级为 `phase2` JSON + 语言描述
- **快捷流程**：支持用户跳过 Phase 2 分析直接获取优化处方，后端静默调用 `phase2` 获取所需数据

### portfolio-deep-diagnosis/SKILL.md

- 参数从纯文本列表改为结构化表格，含 JSON 字段名、可选值、中文选项一一对应
- 执行流程明确：先 `phase2_pdf`，失败再降级 `phase2`

### portfolio-optimization/SKILL.md

- 约束参数表格化，6 个字段全部对齐实际代码中的 `UserConstraints` 数据结构
- 补充了 `objectives` 默认值推导逻辑（基于 `risk_tolerance`）

---

## 新增文件

| 文件 | 用途 |
|------|------|
| `call_remote_phase_api.py` | 远端 API HTTP 桥接脚本（替代本地 60+ 个 Python 文件） |
| `docs.call-remote-phase-api.md` | 桥接脚本的运行原理文档 |
| `docs.portfolio-health-check-api.md` | 三个 RESTful API 的接口规范 |
| `DEPLOY_OPENCLAW_CLIENT.md` | 客户端部署指南 |

## [2026-04-13] 固定 API 地址回退

- `call_remote_phase_api.py` 现在默认回退到固定服务器 `http://82.157.41.134:9000`
- `PORTFOLIO_API_BASE_URL` 和 `--base-url` 仍然可以覆盖默认值
- 这样在固定部署环境下，直接调用 skill 不会因为缺少 API 地址配置而报错

## 删除文件

| 目录/文件 | 原因 |
|----------|------|
| `scripts/portfolio-health-check/` (60+ 文件) | 计算代码已封装到远端 API 服务器，客户端不再需要 |
| `.cursor/skills/portfolio-health-check/state/` | 重复的 state 文件，统一使用 `state/` 目录 |

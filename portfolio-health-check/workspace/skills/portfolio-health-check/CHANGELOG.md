# Portfolio Health Check — 变更说明

> 更新日期: 2026-04-10

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

## 删除文件

| 目录/文件 | 原因 |
|----------|------|
| `scripts/portfolio-health-check/` (60+ 文件) | 计算代码已封装到远端 API 服务器，客户端不再需要 |
| `.cursor/skills/portfolio-health-check/state/` | 重复的 state 文件，统一使用 `state/` 目录 |

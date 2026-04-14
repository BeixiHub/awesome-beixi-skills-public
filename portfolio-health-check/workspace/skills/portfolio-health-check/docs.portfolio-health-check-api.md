# Portfolio Health Check — RESTful API 接口规范

> 版本: v1.2 | 日期: 2026-04-14 | BeixiAI

本文档定义持仓健康检查三个阶段的接口规范。Phase 2 与 Phase 3 通过远端 RESTful JSON 接口提供，Phase 1 由 Skill 本地驱动，无独立远端接口。

---

## 通用约定

### 持仓输入格式（Phase 2 / Phase 3 复用）

```json
{
  "holdings": [
    { "code": "600519.SH", "weight_pct": 30.0 },
    { "code": "300750.SZ", "weight_pct": 25.0 }
  ],
  "cash_pct": 15.0
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `holdings` | `array` | 是 | 持仓列表 |
| `holdings[].code` | `string` | 是 | 标准代码，格式 `{ticker}.{SH\|SZ}`，ETF 同理 |
| `holdings[].weight_pct` | `number` | 是 | 持仓占比（%），范围 `(0, 100]` |
| `cash_pct` | `number` | 否 | 现金占比（%），默认 `0` |

说明：

- 对外接口建议以 `code` 作为唯一资产标识，不要求额外提供 `name`
- 当前实现要求 `holdings` 非空，且每个 holding 至少包含 `code` 与数值型 `weight_pct`

### 通用响应包装

```json
{
  "status": "ok",
  "error_message": null,
  "data": { }
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `status` | `string` | `"ok"` 或 `"error"` |
| `error_message` | `string \| null` | 失败时返回错误描述，成功时为 `null` |
| `data` | `object \| null` | 业务数据，结构由各接口自行定义，失败时为 `null` |

说明：

- Phase 2 成功响应除上述字段外，还会额外返回 `client_output`、`_internal`、`artifacts`、`pipeline`
- Phase 3 成功响应的主要业务内容位于 `data` 内

### 认证

所有接口支持可选的 Bearer Token 认证：

```
Authorization: Bearer <token>
```

当前默认部署无需认证。如果服务端启用认证，则需在请求头中携带 token。

---

## 客户端调用方式

Skill 通过 `call_remote_phase_api.py` 脚本统一调用 Phase 2 / Phase 3 远端接口。Phase 1 无远端 API，由 Skill 本地完成。

### 支持的 phase 值

| phase 值 | 对应端点 | 返回格式 |
|----------|---------|---------|
| `phase2` | `POST /api/v1/phase-2/deep-diagnosis` | JSON |
| `phase2_pdf` | `POST /api/v1/phase-2/deep-diagnosis/pdf` | PDF（`application/pdf`） |
| `phase3` | `POST /api/v1/phase-3/optimization` | JSON |

### 命令行用法

```bash
python call_remote_phase_api.py <phase> <payload_file> [--output <path>] [--base-url <url>] [--token <token>]
```

| 参数 | 说明 |
|------|------|
| `phase` | `phase2` \| `phase2_pdf` \| `phase3` |
| `payload_file` | JSON payload 文件路径 |
| `--output` | 可选，输出文件路径。`phase2`/`phase3` 写 JSON，`phase2_pdf` 写 PDF 二进制 |
| `--base-url` | 可选，远端 API 基地址 |
| `--token` | 可选，Bearer token |

### 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `PORTFOLIO_API_BASE_URL` | 远端 API 基地址 | `http://82.157.41.134:9000` |
| `PORTFOLIO_API_TOKEN` | Bearer token | 空（不认证） |

### 超时与重试

- 请求超时：**180 秒**
- 超时自动重试：**最多 2 次**（仅对 `TimeoutError` / `socket.timeout` 重试）
- HTTP 错误和连接错误**不重试**

### 典型调用示例

```bash
# Phase 2 — PDF 报告
python call_remote_phase_api.py phase2_pdf state/phase2_payload.json --output state/phase2_report.pdf

# Phase 2 — JSON（PDF 失败时降级，或快捷流程静默调用）
python call_remote_phase_api.py phase2 state/phase2_payload.json --output state/phase2_result.json

# Phase 3 — 优化处方
python call_remote_phase_api.py phase3 state/phase3_payload.json --output state/phase3_result.json
```

---

## 阶段一：快速诊断（Skill 本地驱动）

Phase 1 **无独立远端 API**，完全由 `portfolio-quick-diagnosis` Skill 在本地完成。

### 执行流程

1. 解析用户持仓输入（支持股票名、代码、占比、股数、金额等多种格式）
2. 调用 QVeris `identify` 接口识别标的（降级为 web search）
3. 通过 web search 补全仓位信息（股价 / 市值）
4. 校验比例合计 100%
5. 输出持仓确认表，等待用户确认
6. 通过 web search 补充个股信息，生成定性分析报告

### 输出

Phase 1 的核心输出是**持仓确认表**，包含以下结构化数据，作为 Phase 2 的输入：

- `holdings[]` — 每只持仓的 `code`（如 `600519.SH`）和 `weight_pct`
- `cash_pct` — 现金占比

以及面向用户的快速诊断报告，涵盖：

- 持仓概览
- 行业分布 / 资产类别构成
- 集中度 / 重叠分析
- 总体评价

---

## 接口二：深度诊断

`POST /api/v1/phase-2/deep-diagnosis`

量化技术分析，需收集 4 个核心分析参数；可选补充组合总市值。

### Request Body

```json
{
  "holdings": [
    { "code": "600519.SH", "weight_pct": 30.0 },
    { "code": "300750.SZ", "weight_pct": 25.0 },
    { "code": "510500.SH", "weight_pct": 20.0 },
    { "code": "600036.SH", "weight_pct": 10.0 }
  ],
  "cash_pct": 15.0,
  "params": {
    "rebalance_frequency": "monthly",
    "position_style": "constant_mix",
    "risk_tolerance": "moderate",
    "investment_horizon": "1-3y",
    "portfolio_market_value": 5000000
  }
}
```

#### `params` 字段说明

| 字段 | 类型 | 必填 | 可选值 | 语义 |
|------|------|------|--------|------|
| `rebalance_frequency` | `string` | 是 | `intraday` \| `weekly` \| `monthly` \| `quarterly` \| `buy_and_hold` | 换仓频率，决定数据维度和回看周期 |
| `position_style` | `string` | 是 | `market_timing` \| `full_rotation` \| `constant_mix` \| `dca` \| `core_satellite` | 仓位管理风格 |
| `risk_tolerance` | `string` | 是 | `conservative` \| `moderate` \| `aggressive` \| `very_aggressive` | 风险承受力，决定报警阈值 |
| `investment_horizon` | `string` | 是 | `<1y` \| `1-3y` \| `3-5y` \| `>5y` | 投资期限 |
| `portfolio_market_value` | `number` | 否 | 正数，组合总市值（CNY），主要用于流动性分析 |

#### payload 校验清单

- `holdings` 数组不为空
- 每个 holding 都有 `code`（格式 `{数字}.SH` 或 `{数字}.SZ`）和 `weight_pct`（正数）
- `cash_pct` 是 0 或正数
- `rebalance_frequency` 是 5 个枚举值之一
- `position_style` 是 5 个枚举值之一
- `risk_tolerance` 是 4 个枚举值之一
- `investment_horizon` 是 4 个枚举值之一
- 如果有 `portfolio_market_value`，必须是正数（单位：元）

### Response

Phase 2 成功时，除通用 envelope 外，响应还包含 `client_output`、`_internal`、`artifacts`、`pipeline` 等扩展字段。`data` 部分结构如下：

```json
{
  "status": "ok",
  "error_message": null,
  "data": {
    "correlation_matrix": {
      "labels": [],
      "matrix": [],
      "high_correlation_pairs": []
    },
    "risk_metrics": {
      "holdings": [],
      "portfolio": {}
    },
    "risk_contribution": {
      "by_holding": [],
      "by_sector": []
    },
    "concentration": {
      "hhi": 0.0,
      "effective_n": 0.0,
      "top_3_pct": 0.0
    },
    "benchmark": {},
    "factor_exposure": {
      "factor_order": [],
      "holdings": {},
      "portfolio": {}
    },
    "sector_exposure": {},
    "liquidity": {
      "holdings": [],
      "portfolio_max_liquidation_days": 0.0
    },
    "intraday_micro": {},
    "risk_flags": [],
    "metadata": {
      "data_frequency": "",
      "annualization_factor": 0,
      "risk_tolerance": "",
      "warnings": []
    }
  },
  "client_output": {
    "title": "组合诊断摘要",
    "headline": "",
    "sections": [],
    "tables": [],
    "markdown": ""
  },
  "_internal": {
    "holding_returns": {},
    "benchmark_returns": null,
    "holdings": [],
    "cash_pct": 0.0,
    "portfolio_market_value": null
  },
  "artifacts": null,
  "pipeline": {
    "as_of": "2026-04-01",
    "requested_rebalance_frequency": "monthly",
    "selected_benchmark": {},
    "qveris_inputs": {}
  }
}
```

说明：

- `data` 是 Phase 3 会继续消费的公共诊断结果
- `client_output` 是面向用户展示的结构化文本
- `_internal` 是 Phase 3 进一步计算所需的内部数据
- `artifacts` 只有显式要求输出目录或报告时才可能非空
- `pipeline` 记录本次运行的上下文

#### `client_output` 结构详解

| 字段 | 类型 | 说明 |
|------|------|------|
| `title` | `string` | 报告标题，如"组合诊断摘要" |
| `headline` | `string` | 一句话总结，如"组合整体风险中等，但行业集中度偏高" |
| `sections` | `array` | 分节内容，每节含 `heading`（标题）和 `bullets`（要点数组） |
| `tables` | `array` | 表格数据，每项含 `title`（表格标题）、`columns`（列名数组）、`rows`（行数据二维数组） |
| `markdown` | `string` | 完整 markdown 格式的报告文本（优先使用此字段展示） |

呈现优先级：`markdown` > `sections` + `tables` 组装。

### PDF 报告端点

`POST /api/v1/phase-2/deep-diagnosis/pdf`

- 请求体与 JSON 深度诊断端点完全一致
- 成功时返回 `application/pdf`，响应头含 `Content-Disposition`
- 失败时仍返回错误状态码和文本/JSON 错误详情
- PDF 是 Skill 默认行为；PDF 失败时降级为 JSON 端点

---

## 接口三：优化处方

`POST /api/v1/phase-3/optimization`

基于深度诊断结果 + 客户约束，生成分层优化建议和前后对比。

### Request Body

```json
{
  "diagnosis_result": {
    "status": "ok",
    "error_message": null,
    "data": {},
    "client_output": {},
    "_internal": {},
    "artifacts": null,
    "pipeline": {}
  },
  "constraints": {
    "allowed_markets": ["A-share"],
    "allowed_instruments": ["stock", "etf"],
    "additional_capital_ratio": "10-30%",
    "objectives": ["growth", "hedge"]
  }
}
```

**重要**：`diagnosis_result` 是 Phase 2 JSON 端点返回的**完整响应**（即 `state/phase2_result.json` 的全部内容），包含 `status`、`data`、`client_output`、`_internal`、`artifacts`、`pipeline` 等所有字段。不是其中某个子字段。

#### `constraints` 字段说明

| 字段 | 类型 | 必填 | 可选值 | 语义 | 默认值 |
|------|------|------|--------|------|--------|
| `allowed_markets` | `string[]` | 是 | `A-share` \| `HK` \| `US` | 可投资市场 | — |
| `allowed_instruments` | `string[]` | 否 | `stock` \| `etf` \| `fund` \| `futures` \| `option` \| `crypto` | 可使用工具 | `["stock", "etf"]` |
| `additional_capital_ratio` | `string` | 否 | `none` \| `10-30%` \| `30-50%` \| `50%+` | 可追加资金比例 | `"none"` |
| `objectives` | `string[]` | 否 | `growth` \| `income` \| `hedge` \| `ipo_base` | 优化目标（多选） | — |
| `allowed_exposure` | `string[]` | 否 | `A-share` \| `HK` \| `US` \| `global` | 可接受敞口 | = `allowed_markets` + `["global"]` |
| `account_permissions` | `string[]` | 否 | `option_account` \| `futures_account` \| `hk_connect` \| `qdii` | 已开通权限 | `[]` |

#### payload 校验清单

- `diagnosis_result` 包含 `status: "ok"`
- `diagnosis_result` 包含 `data` 字段（非 null）
- `diagnosis_result` 包含 `_internal` 字段（推荐，缺少会导致 rescore 和 stress test 被跳过）
- `constraints.allowed_markets` 非空数组，值为 `A-share`/`HK`/`US` 之一或组合
- `constraints.allowed_instruments` 值为 6 个枚举值之一或组合
- `constraints.additional_capital_ratio` 是 4 个枚举值之一
- `constraints.objectives` 值为 4 个枚举值之一或组合

兼容性说明：

- 服务端仍兼容旧格式 `investable_markets` / `available_capital` / 旧枚举 `objectives`
- 新接入方建议统一使用上述格式

### Response

Phase 3 成功响应结构如下：

```json
{
  "status": "ok",
  "error_message": null,
  "data": {
    "recommendations": {},
    "exclusive_groups": [],
    "asset_alignment": {},
    "constraints_applied": {},
    "summary": {},
    "client_output": {},
    "execution_info": {
      "rescore_executed": true,
      "stress_test_executed": true,
      "warnings": []
    }
  }
}
```

说明：

- `data.client_output` 是面向用户展示的结构化文本，呈现方式同 Phase 2（优先 `markdown`，否则 `sections` + `tables` 组装）
- `data.execution_info.warnings` 非空时需向用户展示警告
- 若 `rescore_executed` 或 `stress_test_executed` 为 `false`，通常因 `_internal` 缺失导致降级

失败时：

```json
{
  "status": "error",
  "error_message": "具体错误信息",
  "data": null
}
```

---

## 数据流总览

```
Phase 1（Skill 本地）
  用户输入 → QVeris identify → web search 补全 → 持仓确认表
  输出：holdings[] + cash_pct

Phase 2（远端 API）
  holdings + cash_pct + params → call_remote_phase_api.py phase2_pdf → PDF 报告
                                → call_remote_phase_api.py phase2     → JSON（降级或静默调用）
  输出：state/phase2_report.pdf + state/phase2_result.json

Phase 3（远端 API）
  phase2_result.json（完整）+ constraints → call_remote_phase_api.py phase3 → JSON
  输出：state/phase3_result.json
```

### 快捷流程

用户在 Phase 1 完成后可跳过 Phase 2 展示，直接进入 Phase 3：

1. 仍需收集 Phase 2 的 4 个参数
2. 静默调用 `phase2`（非 `phase2_pdf`）获取 JSON
3. 不向用户展示 Phase 2 结果
4. 直接进入 Phase 3 约束收集和执行

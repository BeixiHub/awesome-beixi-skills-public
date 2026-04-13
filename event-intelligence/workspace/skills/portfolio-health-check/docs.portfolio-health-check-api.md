# Portfolio Health Check — RESTful API 接口规范

> 版本: v1.1 | 日期: 2026-04-07 | BeixiAI

本文档定义三个无状态 RESTful JSON 接口，覆盖持仓健康检查的三个阶段。所有接口均为 `POST` 方法，`Content-Type: application/json`。

---

## 通用约定

### 持仓输入格式（三个接口复用）

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

---

## 接口一：快速诊断

`POST /api/v1/portfolio/quick-diagnosis`

LLM 提示词驱动的快速分析，输出持仓概览、集中度分析、总体评价。

### Request Body

```json
{
  "holdings": [
    { "code": "600519.SH", "weight_pct": 30.0 },
    { "code": "300750.SZ", "weight_pct": 25.0 },
    { "code": "510500.SH", "weight_pct": 20.0 },
    { "code": "600036.SH", "weight_pct": 10.0 }
  ],
  "cash_pct": 15.0
}
```

无额外参数，仅需持仓 + 现金。

### Response（`data` 部分结构不做强制约定，以下为参考）

Phase 1 当前更偏向由 LLM / skill 驱动的快速诊断阶段，输出以结构化文本结果为主。若将其对外服务化，返回内容通常会围绕以下信息组织：

- 行业分布
- 资产类别构成
- 集中度/重叠分析
- 总体评价
- 标的识别与持仓确认相关信息

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

### PDF 报告端点

`POST /api/v1/phase-2/deep-diagnosis/pdf`

- 请求体与 JSON 深度诊断端点完全一致
- 成功时返回 `application/pdf`
- 失败时仍返回错误状态码和文本/JSON 错误详情

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
    "_internal": {}
  },
  "constraints": {
    "allowed_markets": ["A-share"],
    "allowed_instruments": ["stock", "etf"],
    "additional_capital_ratio": "10-30%",
    "objectives": ["growth", "hedge"]
  }
}
```

#### `constraints` 字段说明（推荐格式）

| 字段 | 类型 | 必填 | 可选值 | 语义 |
|------|------|------|--------|------|
| `allowed_markets` | `string[]` | 是 | `A-share` \| `HK` \| `US` | 可投资市场 |
| `allowed_exposure` | `string[]` | 否 | `A-share` \| `HK` \| `US` \| `global` | 可接受敞口；默认 = `allowed_markets + global` |
| `allowed_instruments` | `string[]` | 否 | `stock` \| `etf` \| `fund` \| `futures` \| `option` \| `crypto` | 可使用工具 |
| `account_permissions` | `string[]` | 否 | `option_account` \| `futures_account` \| `hk_connect` \| `qdii` | 已开通权限 |
| `additional_capital_ratio` | `string` | 否 | `none` \| `10-30%` \| `30-50%` \| `50%+` | 可追加资金比例 |
| `objectives` | `string[]` | 否 | `growth` \| `income` \| `hedge` \| `ipo_base` | 优化目标（多选） |

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

失败时：

```json
{
  "status": "error",
  "error_message": "具体错误信息",
  "data": null
}
```

补充说明：

- `diagnosis_result` 是 Phase 3 的推荐上游输入
- 若 `diagnosis_result` 中缺失 `_internal`，Phase 3 仍可运行，但会降级，`execution_info` 中会体现相关警告

---
name: portfolio-optimization
description: 收集约束后，通过运行 call_remote_phase_api.py 调用远端 Phase 3 API，输出优化处方。
---

# 投资组合优化处方

## 前提

本阶段需要 `PORTFOLIO_API_KEY`。未配置则禁止执行，向用户提示：
```
抱歉，优化处方需要蓓曦星途平台 API Key 才能运行。请前往 https://deepseekdata.com/arena.html 注册账号并充值，然后点击右上角的「API 开放平台」获取 API Key，把 Key 发给我即可继续。
```

阶段二已完成。需要以下数据：

- **diagnosis_result**：`state/{run_id}/phase2_result.json` 的**完整内容**（整个文件就是 `diagnosis_result` 的值）
- 该文件必须包含 `data` 和 `_internal` 字段。缺少 `_internal` 时 Phase 3 降级运行（rescore 和 stress test 被跳过）

`run_id` 在阶段二已生成，继续沿用。

## 收集约束

约束收集由总控 SKILL.md 驱动。以下是字段名与合法值的对照：

| 收集项 | JSON 字段 | 合法值 | 选择方式 |
|--------|----------|--------|---------|
| 可投资市场 | `allowed_markets` | `A-share` / `HK` / `US` | 多选 |
| 可使用工具 | `allowed_instruments` | `stock` / `etf` / `fund` / `futures` / `option` / `crypto` | 多选，默认 `["stock", "etf"]` |
| 剩余可投资金 | `additional_capital_ratio` | `none` / `10-30%` / `30-50%` / `50%+` | 单选，默认 `"none"` |
| 投资目标 | `objectives` | `growth` / `income` / `hedge` / `ipo_base` | 多选 |

可选字段（用户主动提到时才收集）：`allowed_exposure`（默认 = `allowed_markets` + `["global"]`）、`account_permissions`（如 `option_account` / `futures_account` / `hk_connect` / `qdii`，默认 `[]`）。

---

## 执行步骤

### 第 1 步：组装 payload JSON

1. 读取 `state/{run_id}/phase2_result.json` 完整内容 → `diagnosis_result`
2. 用收集到的约束构建 `constraints`
3. 写入 `state/{run_id}/phase3_payload.json`：

```json
{
  "diagnosis_result": <phase2_result.json 的完整内容>,
  "constraints": {
    "allowed_markets": ["A-share", "HK"],
    "allowed_instruments": ["stock", "etf"],
    "additional_capital_ratio": "10-30%",
    "objectives": ["growth", "income"]
  }
}
```

**写入前校验**：
- `diagnosis_result` 包含 `status: "ok"` 和 `data` 字段
- `constraints.allowed_markets` 非空，值为合法枚举
- `constraints.allowed_instruments` 值为合法枚举
- `constraints.additional_capital_ratio` 为合法枚举
- `constraints.objectives` 非空

### 第 2 步：运行 Python 脚本

```
正在生成优化方案，通常需要约 7 分钟，请稍候...
```

如果轮询过程中发现任务处于排队状态（`queue > 0`），追加提醒：
```
当前有其他任务正在排队，等待时间可能延长，请耐心等候。
```

```bash
python call_remote_phase_api.py phase3 state/{run_id}/phase3_payload.json --output state/{run_id}/phase3_result.json
```

### 第 3 步：呈现结果

脚本运行成功后，会自动生成两个文件：
- `state/{run_id}/phase3_result.json` — 完整结构化数据
- `state/{run_id}/phase3_result.md` — 服务器端已格式化好的完整中文报告

**直接读取 `state/{run_id}/phase3_result.md` 并逐字原样输出给用户。不要改写、删减、概括或重新组织内容。** 这份报告包含阅读指引、综合研判、配置建议、建议卡片（含前后对比表和压力测试）等完整内容，是最终成品。

如果 `.md` 文件不存在，降级从 JSON 的 `data.client_output.markdown` 字段读取并原样输出。

不要把原始 JSON 贴给用户。

### 第 4 步：收口

用总控 SKILL.md 中的收尾话术结束。

---

## 错误处理

| 错误 | 用户提示语 | 后续动作 |
|------|----------|---------|
| 连接失败 | "优化服务暂时不可用，建议稍后再试。" | 不重试 |
| HTTP 4xx | "请求格式有误，我来检查并修正。" | 校验 JSON，修正后重试一次 |
| HTTP 5xx | "服务器内部错误，请稍后重试。" | 不重试 |
| `status: "error"` | "优化生成失败：{error_message}" | 建议检查 Phase 2 数据 |
| `_internal` 缺失 | "部分高级分析因数据不完整被跳过。" | 结果仍可用 |

## 禁止事项

- **禁止**在本地自行生成优化建议。
- **禁止**跳过 `call_remote_phase_api.py` 用其他方式调用 API。

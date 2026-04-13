---
name: portfolio-optimization
description: 收集约束后，通过运行 call_remote_phase_api.py 调用远端 Phase 3 API，输出优化处方。
---

# 投资组合优化处方

## 前提

阶段二已完成。你需要从 Phase 2 获取以下数据：

- **diagnosis_result**：`state/phase2_result.json` 文件的**完整内容**
  - 注意：不是其中某个子字段，而是整个文件的内容就是 `diagnosis_result` 的值
  - 这个文件必须包含 `data` 和 `_internal` 字段。如果缺少 `_internal`，Phase 3 会降级运行（rescore 和 stress test 会被跳过）

## 收集约束

约束收集的交互方式在总控 SKILL.md 中已有完整脚本。以下是映射表供参考：

| 收集项 | JSON 字段 | 可选值 | 中文选项 | 选择方式 |
|--------|----------|--------|---------|---------|
| 可投资市场 | `allowed_markets` | `A-share` / `HK` / `US` | A股 / 港股通 / 美股 | 多选 |
| 可使用工具 | `allowed_instruments` | `stock` / `etf` / `fund` / `futures` / `option` / `crypto` | 股票 / ETF / 基金 / 期货 / 期权 / 加密货币 | 多选，默认 `["stock", "etf"]` |
| 剩余可投资金 | `additional_capital_ratio` | `none` / `10-30%` / `30-50%` / `50%+` | 满仓无余量 / 还有10-30% / 还有30-50% / 还有50%以上 | 单选，默认 `"none"` |
| 投资目标 | `objectives` | `growth` / `income` / `hedge` / `ipo_base` | 资产增值 / 稳定现金流 / 对冲已有风险 / 打新底仓 | 多选 |

**可选补充约束**（一般不需要主动问，用户提到时才收集）：

| 收集项 | JSON 字段 | 可选值 | 默认值 |
|--------|----------|--------|--------|
| 可接受敞口 | `allowed_exposure` | `A-share` / `HK` / `US` / `global` | = `allowed_markets` + `["global"]` |
| 已开通权限 | `account_permissions` | `option_account` / `futures_account` / `hk_connect` / `qdii` | `[]` |

**多选交互示例**：

用户回复 "1,2" → 对应数组 `["A-share", "HK"]`
用户回复 "1" → 对应数组 `["A-share"]`
用户只说 "A 股" → 对应数组 `["A-share"]`

---

## 执行步骤（严格按此执行）

### 第 1 步：组装 payload JSON

**构建规则**：
1. 读取 `state/phase2_result.json` 的完整内容 → 这就是 `diagnosis_result` 字段的值
2. 用收集到的约束构建 `constraints` 对象
3. 合并为一个 JSON 并写入 `state/phase3_payload.json`

**将以下内容写入文件 `state/phase3_payload.json`**：

```json
{
  "diagnosis_result": <state/phase2_result.json 的完整内容>,
  "constraints": {
    "allowed_markets": ["A-share", "HK"],
    "allowed_instruments": ["stock", "etf"],
    "additional_capital_ratio": "10-30%",
    "objectives": ["growth", "income"]
  }
}
```

**具体操作**：
1. 先读取 `state/phase2_result.json` 的内容（一个完整的 JSON 对象）
2. 将其作为 `diagnosis_result` 的值
3. 将收集到的约束作为 `constraints` 的值
4. 写入 `state/phase3_payload.json`

**写入前的校验清单**：
- [ ] `diagnosis_result` 包含 `status: "ok"`
- [ ] `diagnosis_result` 包含 `data` 字段（非 null）
- [ ] `diagnosis_result` 包含 `_internal` 字段（推荐，缺少会降级）
- [ ] `constraints.allowed_markets` 非空数组，值为 `A-share`/`HK`/`US` 之一或组合
- [ ] `constraints.allowed_instruments` 值为 6 个枚举值之一或组合
- [ ] `constraints.additional_capital_ratio` 是 4 个枚举值之一
- [ ] `constraints.objectives` 值为 4 个枚举值之一或组合

### 第 2 步：告知用户正在生成

```
正在生成优化方案，请稍候...
```

### 第 3 步：运行 Python 脚本调用远端 API

```bash
python call_remote_phase_api.py phase3 state/phase3_payload.json --output state/phase3_result.json
```

- 这会将 payload POST 到远端服务器，服务器返回优化处方 JSON
- 结果保存到 `state/phase3_result.json`

### 第 4 步：读取并呈现结果

从 `state/phase3_result.json` 读取结果。

**结果结构**：

```json
{
  "status": "ok",
  "error_message": null,
  "data": {
    "recommendations": { },
    "exclusive_groups": [],
    "asset_alignment": { },
    "constraints_applied": { },
    "summary": { },
    "client_output": { },
    "execution_info": {
      "rescore_executed": true,
      "stress_test_executed": true,
      "warnings": []
    }
  }
}
```

**呈现方式**：

优先使用 `data.client_output`。如果 `client_output` 中有 `markdown` 字段，直接输出。

否则按以下格式组装：

```markdown
## 优化处方

{client_output.headline 或 summary 的内容}

### 第一层：零成本操作（无需额外资金）
{从 recommendations 中提取优先级最高的建议}

### 第二层：需要额外资金
{从 recommendations 中提取需要资金的建议}

### 第三层：需要新品种/新账户
{从 recommendations 中提取需要新权限的建议}

---

⚠️ 以上建议仅供参考，不构成具体买卖指令。
```

**如果 `execution_info.warnings` 不为空**，在结果末尾补充：
```
注意：{warnings 的内容}
```

**绝对不要**把原始 JSON 贴给用户。

### 第 5 步：收口

结果解释完毕，会话结束：
```
以上是基于您当前持仓和投资约束的优化建议，仅供参考，不构成具体买卖指令。如果您有任何疑问，欢迎随时讨论。
```

---

## 常见场景处理

| 场景 | 处理方式 |
|------|---------|
| 用户想改约束重跑 | "好的，我帮您修改约束重新生成方案。" 更新 JSON，重新执行第 3-5 步 |
| 用户问"这个建议靠谱吗" | "这些建议是基于量化分析模型生成的，仅供参考。具体操作请结合您自身情况和专业顾问意见。" |
| `execution_info` 显示 rescore 未执行 | 结果末尾补充："注意：由于部分分析数据不完整，优化方案未经压力测试验证。" |
| 用户想回去看 Phase 2 结果 | 从 `state/phase2_result.json` 读取 `client_output` 重新呈现 |

## 错误处理

| 错误 | 用户提示语 | 后续动作 |
|------|----------|---------|
| 连接失败 | "优化服务暂时不可用，建议稍后再试。" | 不重试 |
| HTTP 4xx | "请求格式有误，我来检查并修正。" | 检查校验清单，修正 JSON，重试一次 |
| HTTP 5xx | "服务器内部错误，请稍后重试。" | 不重试 |
| 超时 | "优化请求超时，我将再试一次。" | 重试一次 |
| 返回 `status: "error"` | "优化生成失败：{error_message}" | 告知用户错误原因，建议检查 Phase 2 数据 |
| `_internal` 缺失导致降级 | "注意：部分高级分析（压力测试）因数据不完整被跳过。建议重新运行深度诊断。" | 结果仍可用，只是不够完整 |

## 禁止事项

- **禁止**在本地自行生成优化建议或处方。
- **禁止**跳过 `call_remote_phase_api.py` 用其他方式调用 API。
- **唯一的执行方式**就是运行上述 Python 命令。

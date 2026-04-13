---
name: portfolio-deep-diagnosis
description: 收集 4 个参数后，通过运行 call_remote_phase_api.py 调用远端 Phase 2 API，返回深度诊断并默认生成 PDF。
---

# 投资组合深度诊断

## 前提

阶段一已完成。你需要从阶段一的持仓确认表中提取以下数据：

- **holdings 数组**：确认表中每一行（非现金行）的代码和占比
  - 例：`{"code": "600519.SH", "weight_pct": 30.0}`
- **cash_pct**：确认表中现金行的占比数值
  - 例：`15.0`

## 收集参数

参数收集的交互方式在总控 SKILL.md 中已有完整脚本（编号选项 → 用户回数字 → 映射 JSON 值）。

**严禁自行编造选项。必须逐字使用下方表格"中文选项"列的文字，不得改写、合并、替换或自创任何选项。** 例如"价值/成长/均衡""低/中/高""每月/每季度/每年"等都不是合法选项。

以下是唯一合法的映射表：

| 收集项 | JSON 字段 | 可选值 | 中文选项 |
|--------|----------|--------|---------|
| 换仓频率 | `rebalance_frequency` | `intraday` / `weekly` / `monthly` / `quarterly` / `buy_and_hold` | 日内 / 周度 / 月度 / 季度 / 长期持有 |
| 仓位管理风格 | `position_style` | `market_timing` / `full_rotation` / `constant_mix` / `dca` / `core_satellite` | 择时空仓型 / 满仓轮动 / 恒定比例 / 定投渐进 / 核心+卫星 |
| 风险承受力 | `risk_tolerance` | `conservative` / `moderate` / `aggressive` / `very_aggressive` | 保守 / 稳健 / 积极 / 激进 |
| 投资期限 | `investment_horizon` | `<1y` / `1-3y` / `3-5y` / `>5y` | <1年 / 1-3年 / 3-5年 / 5年以上 |
| 总投资金额（可选） | `portfolio_market_value` | 正数（元） | 用户输入万元 → 乘以 10000 转换为元 |

---

## 执行步骤（严格按此执行）

### 第 1 步：组装 payload JSON

从阶段一的确认表和收集到的参数，构建 JSON 文件。

**构建规则**：
- 确认表中每一行（现金除外）→ holdings 数组的一个元素：`{"code": "代码", "weight_pct": 占比数字}`
- 确认表中现金行的占比 → `cash_pct`
- 收集到的 4 个参数 → `params` 对象
- 用户给了总投资金额 → `params.portfolio_market_value`（万元 × 10000）

**将以下内容写入文件 `state/phase2_payload.json`**：

```json
{
  "holdings": [
    { "code": "600519.SH", "weight_pct": 30.0 },
    { "code": "300750.SZ", "weight_pct": 25.0 },
    { "code": "002594.SZ", "weight_pct": 20.0 }
  ],
  "cash_pct": 10.0,
  "params": {
    "rebalance_frequency": "monthly",
    "position_style": "constant_mix",
    "risk_tolerance": "moderate",
    "investment_horizon": "1-3y",
    "portfolio_market_value": 500000
  }
}
```

**写入前的校验清单**（逐项检查）：
- [ ] `holdings` 数组不为空
- [ ] 每个 holding 都有 `code`（格式 `{数字}.SH` 或 `{数字}.SZ`）和 `weight_pct`（正数）
- [ ] `cash_pct` 是 0 或正数
- [ ] `rebalance_frequency` 是 5 个枚举值之一
- [ ] `position_style` 是 5 个枚举值之一
- [ ] `risk_tolerance` 是 4 个枚举值之一
- [ ] `investment_horizon` 是 4 个枚举值之一
- [ ] 如果有 `portfolio_market_value`，必须是正数（单位：元）

### 第 2 步：告知用户正在分析

在运行命令前，告诉用户：
```
正在进行深度诊断分析，这需要约 30-60 秒，请稍候...
```

### 第 3 步：运行 Python 脚本生成 PDF

```bash
python call_remote_phase_api.py phase2_pdf state/phase2_payload.json --output state/phase2_report.pdf
```

- 这会将 payload POST 到远端服务器，服务器返回 PDF 文件
- PDF 保存到 `state/phase2_report.pdf`
- 命令成功（退出码 0）→ 进入第 5 步
- 命令失败（非零退出码）→ 进入第 4 步

### 第 4 步：PDF 失败时降级为 JSON

仅当第 3 步报错时执行：

```bash
python call_remote_phase_api.py phase2 state/phase2_payload.json --output state/phase2_result.json
```

告诉用户：
```
PDF 报告生成遇到问题，我将直接为您解读诊断结果。
```

### 第 5 步：向用户呈现结果

**如果 PDF 成功生成**（第 3 步成功）：

同时运行第 4 步的 JSON 命令获取结构化数据用于解读，然后向用户呈现：

```
深度诊断报告已生成：state/phase2_report.pdf

以下是核心发现摘要：
```

然后从 JSON 结果的 `client_output` 字段提取内容做解读。

**如果只有 JSON**（第 3 步失败，第 4 步成功）：

从 `state/phase2_result.json` 读取结果，找到 `client_output` 字段。

**`client_output` 的结构**：

```json
{
  "client_output": {
    "title": "组合诊断摘要",
    "headline": "一句话总结，如：组合整体风险中等，但行业集中度偏高",
    "sections": [
      {
        "heading": "板块标题",
        "bullets": ["要点1", "要点2"]
      }
    ],
    "tables": [
      {
        "title": "表格标题",
        "columns": ["列1", "列2"],
        "rows": [["值1", "值2"]]
      }
    ],
    "markdown": "完整 markdown 格式的报告文本"
  }
}
```

**呈现方式**：

如果 `client_output.markdown` 不为空，直接输出 markdown 内容。

否则，按以下格式组装：

```markdown
## 深度诊断结果

{client_output.headline}

### {sections[0].heading}
{逐条列出 bullets}

### {sections[1].heading}
{逐条列出 bullets}

### {tables[0].title}
| {columns[0]} | {columns[1]} | ... |
|---|---|---|
| {rows[0][0]} | {rows[0][1]} | ... |
```

**绝对不要**把原始 JSON 贴给用户。

### 第 6 步：收口

结果解释完毕后，用总控 SKILL.md 中的过渡话术询问是否进入优化处方。

---

## 常见场景处理

| 场景 | 处理方式 |
|------|---------|
| 用户想改参数重跑 | "好的，我帮您修改参数重新分析。" 更新 JSON，重新执行第 3-5 步 |
| 结果显示无风险提示 | "好消息：当前组合未触发高风险提示。" 仍然展示关键指标 |
| 用户问某个指标含义 | 用通俗中文解释，如"夏普比率衡量每承受一份风险能获得多少超额收益" |
| 用户只想看某一项 | 从 JSON 结果中提取对应部分展示 |

## 错误处理

| 错误 | 用户提示语 | 后续动作 |
|------|----------|---------|
| 连接失败（URLError） | "分析服务暂时不可用，可能是服务器维护中。建议稍后再试。" | 不重试 |
| HTTP 4xx（payload 格式错误） | "请求格式有误，我来检查并修正。" | 检查校验清单，修正 JSON，重试一次 |
| HTTP 5xx（服务端内部错误） | "服务器内部错误，请稍后重试。" | 不重试 |
| 超时（180 秒） | "分析请求超时，可能是持仓数量较多。我将再试一次。" | 重试一次 |
| PDF 返回非 PDF 内容 | （不告知用户）自动降级为 JSON 模式（第 4 步） | 执行第 4 步 |

## 禁止事项

- **禁止**询问用户"是否需要 PDF"——PDF 是默认行为，不是选项。
- **禁止**在本地自行生成 PDF、HTML 或任何报告文件。
- **禁止**跳过 `call_remote_phase_api.py` 用其他方式调用 API。
- **唯一的执行方式**就是运行上述 Python 命令。

---
name: portfolio-deep-diagnosis
description: 收集 4 个参数后，通过运行 call_remote_phase_api.py 调用远端 Phase 2 API，返回深度诊断并默认生成 PDF。
---

# 投资组合深度诊断

## 前提

本阶段需要 `PORTFOLIO_API_KEY`。未配置则禁止执行，向用户提示：
```
抱歉，深度诊断需要蓓曦星途平台 API Key 才能运行。请前往 https://deepseekdata.com/arena.html 注册账号并充值，然后点击右上角的「API 开放平台」获取 API Key，把 Key 发给我即可继续。
```

阶段一已完成。从持仓确认表中提取：

- **holdings 数组**：每一行（非现金行）的代码和占比，如 `{"code": "600519.SH", "weight_pct": 30.0}`
- **cash_pct**：现金行的占比数值

## 收集参数

参数收集由总控 SKILL.md 驱动（编号选项 → 用户回数字 → 映射 JSON 值）。以下是字段名与合法值的对照：

| 收集项 | JSON 字段 | 合法值 |
|--------|----------|--------|
| 换仓频率 | `rebalance_frequency` | `intraday` / `weekly` / `monthly` / `quarterly` / `buy_and_hold` |
| 仓位管理风格 | `position_style` | `market_timing` / `full_rotation` / `constant_mix` / `dca` / `core_satellite` |
| 风险承受力 | `risk_tolerance` | `conservative` / `moderate` / `aggressive` / `very_aggressive` |
| 投资期限 | `investment_horizon` | `<1y` / `1-3y` / `3-5y` / `>5y` |
| 总投资金额（可选） | `portfolio_market_value` | 正数（元），用户输入万元 → ×10000 |

---

## 执行步骤

### 第 1 步：生成 run_id 并组装 payload

生成本次分析的 `run_id`（后续所有文件存入 `state/{run_id}/`）：
```bash
python -c "import uuid; print(uuid.uuid4().hex[:8])"
```

创建目录并写入 `state/{run_id}/phase2_payload.json`：

```json
{
  "holdings": [
    { "code": "600519.SH", "weight_pct": 30.0 },
    { "code": "300750.SZ", "weight_pct": 25.0 }
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

**写入前校验**：
- `holdings` 非空，每个元素有 `code`（格式 `{数字}.SH` 或 `{数字}.SZ`）和 `weight_pct`（正数）
- `cash_pct` ≥ 0
- 四个参数字段各为合法枚举值
- 如有 `portfolio_market_value`，必须为正数（单位：元）
- `position_style` × `rebalance_frequency` 通过兼容性校验（见下表）

**参数兼容性矩阵**（服务器强制校验，客户端必须预检）：

| | intraday | weekly | monthly | quarterly | buy_and_hold |
|---|---|---|---|---|---|
| market_timing | ✅ | ✅ | ✅ | ✅ | ✅ |
| full_rotation | ❌ | ✅ | ✅ | ✅ | ⚠️ |
| constant_mix | ✅ | ✅ | ✅ | ✅ | ❌ |
| dca | ❌ | ✅ | ✅ | ✅ | ❌ |
| core_satellite | ❌ | ✅ | ✅ | ✅ | ⚠️ |

- ❌ 不合法，禁止提交。只重问问题 1 和 2，告知冲突原因
- ⚠️ 可提交但系统切换为漂移监测模式，提交前告知用户

### 第 2 步：告知用户预计等待时间

告知用户：
```
收到，正在进行深度诊断分析。通常需要约 5 分钟，请稍候...
```

如果轮询过程中发现任务处于排队状态（`queue > 0`），追加提醒：
```
当前有其他任务正在排队，等待时间可能延长，请耐心等候。
```

### 第 3 步：运行 Python 脚本生成 PDF

```bash
python call_remote_phase_api.py phase2_pdf state/{run_id}/phase2_payload.json --output state/{run_id}/phase2_report.pdf
```

- 成功（退出码 0）→ 第 5 步
- 失败 → 第 4 步

### 第 4 步：PDF 失败时降级为 JSON

```bash
python call_remote_phase_api.py phase2 state/{run_id}/phase2_payload.json --output state/{run_id}/phase2_result.json
```

告诉用户："PDF 报告生成遇到问题，我将直接为您解读诊断结果。"

### 第 5 步：向用户呈现结果

**PDF 成功时**：

同时运行第 4 步的 JSON 命令获取结构化数据，然后向用户发送 PDF 并附摘要：
```
深度诊断报告已生成：state/{run_id}/phase2_report.pdf

以下是核心发现摘要：
```
从 JSON 结果的 `client_output` 字段提取内容做解读。

**仅 JSON 时**：

从 `state/{run_id}/phase2_result.json` 读取 `client_output`：

```json
{
  "client_output": {
    "title": "组合诊断摘要",
    "headline": "一句话总结",
    "sections": [{ "heading": "板块标题", "bullets": ["要点1", "要点2"] }],
    "tables": [{ "title": "表格标题", "columns": ["列1"], "rows": [["值1"]] }],
    "markdown": "完整 markdown 报告文本"
  }
}
```

- `client_output.markdown` 非空 → 直接输出
- 否则按 sections/tables 组装 markdown

不要把原始 JSON 贴给用户。

### 第 6 步：收口

用总控 SKILL.md 中的过渡话术询问是否进入优化处方。

---

## 常见场景处理

| 场景 | 处理方式 |
|------|---------|
| 用户想改参数重跑 | 更新 JSON，重新执行第 3-5 步 |
| 用户问某个指标含义 | 用通俗中文解释 |
| 用户只想看某一项 | 从 JSON 结果中提取对应部分展示 |

## 错误处理

| 错误 | 用户提示语 | 后续动作 |
|------|----------|---------|
| 连接失败（URLError） | "分析服务连接失败，建议稍后再试。" | 客户端已自动重试 1 次（同一幂等 key，不重复扣费） |
| HTTP 4xx 含"积分不足" | "开放平台积分不足，请先充值。" | 不重试 |
| HTTP 4xx（格式错误） | "请求格式有误，我来检查并修正。" | 校验 JSON，修正后重试一次 |
| HTTP 5xx | "服务器内部错误，请稍后重试。" | 不重试 |
| PDF 返回非 PDF 内容 | （不告知用户）自动降级为 JSON | 执行第 4 步 |

## 禁止事项

- **禁止**询问用户"是否需要 PDF"——PDF 是默认行为。
- **禁止**在本地自行生成 PDF、HTML 或任何报告文件。
- **禁止**跳过 `call_remote_phase_api.py` 用其他方式调用 API。

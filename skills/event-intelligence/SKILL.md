---
name: event-intelligence
description: 定时获取事件资讯并通过可配置消息渠道推送；默认使用 OpenClaw 微信渠道，也保留飞书渠道；支持手动获取事件资讯和查询特定事件资讯详情。
---

# Event Intelligence

## 概述

本技能提供三类能力：

1. **自动推送、手动获取**：按运行时配置抓取最近事件，格式化后投递到配置的消息渠道。
2. **事件详情查询**：用户对某条推送事件感兴趣时，通过历史记录定位 `eventId`，再调用 deepseekdata 详情接口。
3. **每日事件统计**：按当前关键词统计过去 24 小时事件密度、S 级、A 级和其他等级数量。

新版将消息渠道从运行时主流程中解耦。事件抓取、格式化、去重、历史落盘保持通用；消息投递由 `channels/` 下的渠道适配器处理。

## 文件结构

```text
event-intelligence/
├── SKILL.md
├── event_query.py
├── push_runtime.py
├── runtime_config.py
├── channels/
│   ├── __init__.py
│   ├── openclaw_weixin.py
│   └── feishu.py
└── state/
    ├── push_config.json
    └── push_history.json
```

`memory/` 只用于开发过程记录，不属于交付内容。

## 通用强约束

- 自动任务不得依赖临时会话轮询。必须由可持久运行的调度机制触发。
- deepseekdata API key 不得在回复、日志或示例中明文展示。
- 消息接收目标属于部署时状态，不得把固定用户、群、webhook、accountId 写死进通用源码或通用文档示例。
- 无新事件时，定时推送和每日统计必须静默跳过，不发送“暂无事件”消息。
- 手动查询可以展示“暂无事件”提示。
- 所有写入配置和历史的时间戳使用北京时间 UTC+8，格式如 `2026-04-09T14:01:30+08:00`。

## 数据源、时间窗与执行边界（必须遵守）

- 使用本 skill 处理事件查询、推送、统计或详情时，唯一允许的数据源是本 skill 代码通过 deepseekdata API 返回的数据。禁止调用其他 skill、web search、浏览器搜索、公开新闻、LLM 自带知识或任何外部资料来补充、修正、改写或替代 deepseekdata API 结果。
- 必须严格执行本 skill 提供的代码路径和命令，包括 `push_runtime.py`、`event_query.py` 及其封装能力。禁止绕过这些代码自行拼请求、改用其他检索工具、手工伪造事件结果或根据经验补全字段。
- 时间窗口必须严格等于用户明确指定的窗口，或严格等于本 skill 已定义的默认/配置窗口。禁止为了获得更多结果而擅自扩大时间窗，也禁止为了减少结果而擅自缩小时间窗；用户没有指定时只能使用文档中明确写出的默认值。
- deepseekdata API 返回空列表、无详情、无新增事件或统计为 0 都是合法结果。必须如实返回“无事件/无数据/无新增事件”的状态；禁止为了避免空结果而再次搜索、换关键词、扩大时间范围、调用其他数据源或编造事件。
- 如果 deepseekdata API 调用失败、key 缺失、网络异常或返回结构异常，只能报告失败原因并停止当前数据生成流程。禁止用其他来源兜底生成事件列表、详情报告或统计结论。

## deepseekdata Key 规则

- 用户通过 `push_runtime.py set-api-key` 保存的 key 优先于环境变量和 OpenClaw 内部配置。
- 只允许 `deepseekdata_api_key`、`DEEPSEEKDATA_API_KEY`、`event_intel_api_key`、`EVENT_INTEL_API_KEY` 这类明确字段作为事件 API key。
- OpenClaw/内部配置中 LLM provider 的泛化 `apiKey` 或 `api_key` 不得当作 deepseekdata 凭据。
- 如果用户提供 key 后仍报 “API Key 不存在”，不要绕过 `push_runtime.py` 或伪造结果；应通过 stdin 或 `--key-file` 重新保存 key，再执行 `manual-push` / `run-once`。

推荐保存方式：

```bash
printf "%s" "<API_KEY>" | python push_runtime.py set-api-key --key -
```

已有本地密钥文件时：

```bash
python push_runtime.py set-api-key --key-file "<KEY_FILE>"
```

## 运行时配置

`state/push_config.json` 的核心字段：

```json
{
  "active": false,
  "schedule": "5m",
  "keywords": ["AI"],
  "page_size": 10,
  "retention_days": 5,
  "daily_summary_time": "09:00",
  "delivery": {
    "channel": "openclaw-weixin",
    "to": "",
    "accountId": ""
  },
  "feishu_webhooks": [],
  "feishu_receive_id": "",
  "feishu_receive_id_type": "chat_id",
  "event_intel_api_key": ""
}
```

调度只支持：

- `5m`：每 5 分钟
- `15m`：每 15 分钟
- `60m`：每 60 分钟
- `24h`：每 24 小时
- `daily-0915`：每天 09:15
- `daily-1245`：每天 12:45
- `daily-1445`：每天 14:45

关键词最多 3 个。超过 3 个时直接请用户删减，不要写入配置。

## 渠道一：OpenClaw 微信渠道（默认）

### 定位

微信渠道使用腾讯 `openclaw-weixin` 插件，不是企业微信机器人，也不是普通 webhook。

扫码绑定后，微信消息由 OpenClaw channel plugin 负责投递。运行时必须使用稳定的 OpenClaw 投递路由：

- `delivery.channel = "openclaw-weixin"`
- `delivery.to = "<微信路由目标>"`，通常形如 `xxx@im.wechat`
- `delivery.accountId = "<扫码绑定后的账号 ID>"`

### 自动推送影响

自动推送功能保留，但职责拆分如下：

- `push_runtime.py` 仍负责抓取事件、关键词过滤、去重、格式化、每日统计、历史落盘和详情查询。
- 微信消息投递由 OpenClaw cron/announce 根据 `delivery` 路由完成。
- 缺少 `delivery.to` 或 `delivery.accountId` 时，自动推送必须报错并要求配置微信接收目标，不能回退到临时会话发送。

### 配置微信目标

```bash
python push_runtime.py set-weixin-target --to "<WEIXIN_ROUTE>" --account-id "<ACCOUNT_ID>"
```

状态检查：

```bash
python push_runtime.py status
```

### 安装调度

微信渠道下：

```bash
python push_runtime.py install-schedule
```

OpenClaw 微信调度必须使用 `sessionTarget: "isolated"`、`payload.kind: "agentTurn"` 和 `delivery.mode: "announce"`。`delivery` 必须同时包含：

- `channel: "openclaw-weixin"`
- `to: "<xxx@im.wechat>"`
- `accountId: "<扫码登录后的 accountId>"`

`install-schedule` 会让 cron job 执行 `run-once --openclaw-output` / `run-daily-summary --openclaw-output`。该输出模式只打印要投递给微信用户的结构化文本；无新事件时打印 `NO_REPLY`，让 OpenClaw 按 cron/announce 语义抑制空消息。不要把微信默认调度降级为本机 crontab。

该命令生成 OpenClaw cron/announce 调度规格，由 Arkclaw/OpenClaw 平台安装和执行。它不写入本机 crontab，因为本机 crontab 无法保证微信扫码账号投递目标。

停止时：

```bash
python push_runtime.py uninstall-schedule
```

运行时会关闭 `active`，同时提示在 Arkclaw/OpenClaw 平台停用对应 cron/announce 任务。

## 渠道二：飞书渠道（保留）

飞书渠道作为 legacy/可选渠道保留，支持两种目标：

1. 飞书 Webhook。
2. 飞书自建应用：App ID、App Secret、receive_id、receive_id_type。

切换到飞书渠道：

```bash
python push_runtime.py configure --channel feishu
```

配置飞书 Webhook：

```bash
printf "%s" "<WEBHOOK_URL>" | python push_runtime.py set-feishu-webhook --url -
```

配置飞书自建应用接收目标：

```bash
python push_runtime.py set-feishu-target --receive-id "<RECEIVE_ID>" --receive-id-type chat_id
```

飞书渠道可继续由 `push_runtime.py install-schedule` 写入本机 crontab，并由 Python 运行时直接发送飞书消息。飞书详细报告如果需要发送，必须使用飞书卡片或富文本能力，不要用普通纯文本消息发送 Markdown 表格。

## 自动推送流程

用户说“开始推送”“启动事件监控”“定时推送”等：

1. 执行 `python push_runtime.py init-config`，已有配置时不会覆盖。
2. 执行 `python push_runtime.py status` 检查：
   - `event_api.has_key=true`
   - `delivery.active_channel.target_configured=true`
3. 如缺少 deepseekdata key，请用户提供并通过 stdin 或 `--key-file` 保存。
4. 如缺少渠道目标：
   - 微信：请用户先完成扫码绑定，并提供 `delivery.to` 与 `delivery.accountId`。
   - 飞书：请用户提供 Webhook URL，或飞书自建应用的 App ID/App Secret/receive_id。
5. 确认关键词、调度档位和条数。
6. 执行：
   ```bash
   python push_runtime.py configure --active --keywords "<KEYWORD1>,<KEYWORD2>" --schedule 5m --page-size 10
   python push_runtime.py run-once --quiet
   python push_runtime.py run-daily-summary --quiet
   python push_runtime.py install-schedule
   ```
7. 回复用户当前渠道、调度档位和可选档位。

## 手动获取事件

用户说“查最近5小时的事件”“最近有什么 AI 事件”“搜一下半导体事件”等：

```bash
python push_runtime.py manual-push --minutes <MINUTES> --keywords "<KEYWORD1>,<KEYWORD2>"
```

只查询和记录历史、不投递消息：

```bash
python push_runtime.py manual-push --minutes <MINUTES> --keywords "<KEYWORD>" --no-delivery
```

`--no-feishu` 是旧参数别名，仅为兼容旧飞书用法保留。

`manual-push` 会把事件写入 `state/push_history.json`，因此后续仍可通过“第 X 条详细看看”查询详情。`--dry-run` 或 `--no-delivery` 不更新已推送索引。

## 每日事件统计

```bash
python push_runtime.py run-daily-summary
```

统计当前关键词过去 24 小时事件，展示：

- 各关键词 S 级、A 级、其他等级和合计
- 多关键词全局去重后的总数
- 重复命中条数

无事件时静默跳过，不向消息渠道发送空提醒。

## 事件详情查询

用户可以说：

- “第 3 条详细看看”
- “那个关于芯片的事件看一下”
- “上一轮推送的第 1 条”
- “长飞光纤那条”

执行：

```bash
python push_runtime.py detail-from-ref --query "<用户原话>"
```

规则：

- 优先从最近推送历史 `state/push_history.json` 定位事件。
- `batches[0]` 是最新批次，不要重新排序。
- 匹配到多条时，列候选让用户选择，不要猜。
- 找到事件后，用返回的 `eventId` 和 `matched_keywords` 调用 deepseekdata 详情接口。
- 如果 `detail-from-ref` 返回 `status=not_found`，明确说明“该事件不在最近推送事件范围内，无法使用 deepseekdata 推送事件详情接口命中”。到此停止详情报告生成，不要改用其他数据源，不要搜索公开网页，不要补写报告。
- API key 或网络失败时，直接说明“事件数据获取失败”及原因，不编造数据。

详情报告需要按中文结构化输出，至少包含：

1. 基本信息：标题、发布时间、信号等级、信号类型。
2. 核心摘要：`original_summary`。
3. 投资逻辑：`investmentLogic`。
4. 推理验证链路：解析 `overallReasoningChain`。
5. 产业链全景：`formatted_tree`。
6. 传导路径与节奏：`transmission_logic`。
7. 逻辑库匹配：`logic_library_output`。
8. 核心标的：`investmentTargetsSummary`，必须使用 Markdown 表格。
9. 关键风险：`keyRisks`。
10. 历史案例参考：`historical_cases_analysis`。

## CLI 速查

```bash
python push_runtime.py status
python push_runtime.py configure --channel openclaw-weixin
python push_runtime.py set-weixin-target --to "<WEIXIN_ROUTE>" --account-id "<ACCOUNT_ID>"
python push_runtime.py configure --channel feishu
python push_runtime.py set-feishu-target --receive-id "<RECEIVE_ID>" --receive-id-type chat_id
python push_runtime.py set-feishu-webhook --url -
python push_runtime.py manual-push --minutes 60 --keywords "AI" --no-delivery
python push_runtime.py install-schedule
python push_runtime.py uninstall-schedule
python push_runtime.py detail-from-ref --query "第1条详细看看"
```

---
name: event-intelligence
description: 定时获取事件资讯并自动推送到飞书；支持手动获取事件资讯和查询特定事件资讯详情。
---

# Event Intelligence — 事件情报技能

## 概述

本技能分为三类能力：

1. **自动推送、手动获取**：按运行时配置，由本地 Python 常驻/系统定时任务执行。用户也可以指定获取最近一段时间内的最新事件。
2. **事件详情查询**：当用户对某条推送的事件感兴趣时，通过 `eventId` 调用详情接口获取完整分析数据。
3. **每日事件统计**：按运行时配置，由本地 Python 常驻/系统定时任务执行。

## 强约束（必须遵守）

- 自动推送和每日事件统计禁止依赖会话轮询或临时 session 级定时器；必须通过 `push_runtime.py` + 系统任务调度（crontab）执行。
- 自动推送的功能必须通过 `push_runtime.py` 执行抓取、结构化、飞书推送、落盘。
- 禁止为事件自动推送创建Gateway cron / `jobs.json` / `agentTurn` / `announce` 任务；这类任务在网页端或 isolated session 中没有稳定飞书会话目标，容易报 `Delivering to Feishu requires target` 或 `device identity required`。
- 禁止把任何固定 chat_id/open_id/webhook 写进 skill 源码、示例任务或通用说明。飞书接收目标必须作为部署时状态配置，由 `push_runtime.py` 从运行时配置、环境变量或内部配置中解析。

## 对外沟通规则（必须遵守）

- 面向普通用户说明配置缺失时，不要提及 `openclaw/openclaw.json`、`openclaw.json`、`state/push_config.json`、`OPENCLAW_CONFIG_PATH` 等内部文件或环境变量；除非用户明确要求调试内部配置路径。
- 飞书接收目标缺失时，使用用户可理解的话术：`请提供飞书接收目标：Webhook URL，或飞书自建应用的 receive_id（群聊或个人）。如果要推送到群聊，请提供群聊 chat_id/receive_id；如果不知道，我可以根据群聊名称或成员协助查找。`
- 如果已检测到飞书 App ID/App Secret，但缺少 receive_id，不要说“在 openclaw.json 中配置”；应说“还缺少飞书接收目标，请提供群聊的 receive_id/chat_id，或提供 Webhook URL。”
- 回复中不得展示 deepseekdata API key 明文；设置完成后只确认“已保存/已配置”。

## 文件结构

```
event-intelligence/
├── SKILL.md              # 本文件，技能说明
├── event_query.py                # 事件查询、详情和每日总结函数
├── push_runtime.py               # 自动推送运行时
└── state/
    ├── push_config.json  # 推送运行时配置（调度、关键词、上次推送时间等）
    └── push_history.json         # 推送历史（保留最近5天）
```

## 前置条件

- Python 3.10+
- 网络可达 `https://admin.deepseekdata.com` 与飞书
- deepseekdata API key 必须提前配置。运行时按内部配置、`state/push_config.json` 中的 `event_intel_api_key` / `api_key` / `deepseekdata_api_key`、进程环境变量 `EVENT_INTEL_API_KEY` / `DEEPSEEKDATA_API_KEY` 的顺序解析；如果用户未提供，必须先提醒用户提供 key，写入配置后才能继续执行事件查询、推送或定时任务。
- 飞书 App Secret 等敏感凭证不写入代码或 `state/push_config.json`；运行时从环境变量或内部配置读取。接收目标 receive_id 和 Webhook 属于部署时状态，只能写入当前安装实例，不得写入通用源码或示例任务。
- 飞书推送支持两种目标：webhook，或自建应用。自建应用需要 App ID、App Secret 和接收目标 receive_id；运行时会用 App ID/App Secret 调用飞书 `tenant_access_token/internal` 获取 token 后再推送消息。

## 环境适配

### 路径约定

本技能中所有文件路径均 **相对于本 SKILL.md 所在目录**（即 `event-intelligence/`）。
执行命令前，agent 应先定位本技能目录：

```bash
SKILL_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]:-$0}")")" && pwd 2>/dev/null || pwd)"
```

如果 agent 是通过读取 SKILL.md 获得路径的，直接取其父目录即可。后续所有 `cd` 均使用该变量，不依赖任何硬编码绝对路径。

### Python 兼容

不同环境中 Python 命令可能是 `python3` 或 `python`，执行前先探测：

```bash
PY=$(command -v python3 2>/dev/null || command -v python 2>/dev/null)
if [ -z "$PY" ]; then echo "错误：未找到 python3 或 python，请先安装 Python 3.10+"; exit 1; fi
```

后续所有 Python 调用统一使用 `$PY` 代替 `python3` / `python`。

### 敏感配置读取规则

- deepseekdata：API key 解析优先级为内部配置、`state/push_config.json` 的 `event_intel_api_key` / `api_key` / `deepseekdata_api_key`、进程环境变量 `EVENT_INTEL_API_KEY` / `DEEPSEEKDATA_API_KEY`。缺失时必须明确要求用户提供 key，并通过 stdin 或文件持久化：`printf "%s" "<API_KEY>" | $PY push_runtime.py set-api-key --key -`；已有本地密钥文件时优先执行 `$PY push_runtime.py set-api-key --key-file "<KEY_FILE>"`。不要把 key 明文放进普通 CLI 参数。
- 飞书 webhook：读取 `state/push_config.json` 的 `feishu_webhooks` 以及 `openclaw/openclaw.json` 中飞书/飞书 Lark 上下文里的 webhook 字段。
- 飞书自建应用：App ID/App Secret 从环境变量或 `openclaw/openclaw.json` 中飞书/飞书 Lark 上下文读取；接收目标 receive_id/receive_id_type 优先读取 `state/push_config.json` 中的部署时配置，其次读取环境变量和 `openclaw/openclaw.json`。拿到 App ID/App Secret 后，运行时调用飞书 `auth/v3/tenant_access_token/internal` 获取 token，再调用 `im/v1/messages` 推送。
- 如果用户提供飞书群聊 receive_id/chat_id，执行 `$PY push_runtime.py set-feishu-target --receive-id "<RECEIVE_ID>" --receive-id-type chat_id` 持久化到当前安装实例；如果用户提供 Webhook URL，通过 stdin 或文件持久化：`printf "%s" "<WEBHOOK_URL>" | $PY push_runtime.py set-feishu-webhook --url -`；已有本地文件时优先执行 `$PY push_runtime.py set-feishu-webhook --url-file "<WEBHOOK_FILE>"`。`set-feishu-webhook` 默认替换现有 webhook 列表；明确需要多个 webhook 时追加 `--append`。这些值属于用户部署状态，不得提交到通用源码。
- `openclaw/openclaw.json` 的默认查找路径为本项目及上级目录中的 `openclaw/openclaw.json` 或 `openclaw.json`；如用户另有位置，先设置 `OPENCLAW_CONFIG_PATH`。

---

## 第一部分：自动推送（Python 独立运行）

### 启动流程

用户说"开始推送"/"启动事件推送"/"开始定时推送"等触发本技能后：

1. **检查密钥和目标**：执行 `$PY push_runtime.py status`，确认 `event_api.has_key=true`；确认 `feishu.webhook_count > 0`，或 `feishu.has_app_id=true`、`feishu.has_app_secret=true`、`feishu.has_receive_id=true`。如果 deepseekdata API key 缺失，先提醒用户提供，并通过 stdin 或 `--key-file` 保存；如果只有飞书 App ID/App Secret 但没有接收目标，提醒用户提供飞书接收目标（Webhook URL，或自建应用的 receive_id/receive_id_type；群聊可提供 chat_id/receive_id）。拿到 receive_id 后执行 `$PY push_runtime.py set-feishu-target --receive-id "<RECEIVE_ID>" --receive-id-type chat_id`；拿到 Webhook URL 后通过 stdin 或 `--url-file` 保存。
2. **读取配置**：读取 `state/push_config.json`，获取当前推送参数。
3. **确认参数**：向用户确认以下参数（如果配置文件已有值，展示当前值并问是否需要修改）：
   - `schedule`：推送调度档位（默认 `5m`），只能选择 `5m`、`15m`、`60m`、`24h`、`daily-0915`、`daily-1245`、`daily-1445`
   - `keywords`：语义检索关键词列表（默认 `["AI"]`，最多 3 个；用户可改为多个主题，比如：AI、半导体、光模块）。如果用户提供超过 3 个关键词，直接回复暂不支持超过 3 个关键词，请用户删减到 3 个以内，不要写入配置。
   - `page_size`：每次推送返回的事件条数（默认 10）
4. **写入配置**：首次使用先执行 `$PY push_runtime.py init-config` 创建模板；再执行 `$PY push_runtime.py configure --active --keywords "<KEYWORD1>,<KEYWORD2>" --schedule <SCHEDULE> --page-size <PAGE_SIZE>` 写入确认后的参数。启动推送时必须设置 `active=true`；每日统计与事件推送同开同停。
5. **立即执行一次推送**：执行 `$PY push_runtime.py run-once --quiet` 获取当前时间窗口内的事件，直接推送飞书并落盘。
6. **立即执行一次每日统计**：执行 `$PY push_runtime.py run-daily-summary --quiet`。每日统计与事件推送同开同停。
7. **安装系统调度**：执行 `$PY push_runtime.py install-schedule`，交给系统定时任务持续运行。
8. **回复用户**：开启成功后必须提示当前调度，并列出可选项，例如：“事件推送服务已开启。当前按 `<当前档位>` 推送。如果有其他需要，可以改成：每5分钟、每15分钟、每60分钟、每24小时、开盘前15分钟(09:15)、下午开盘前15分钟(12:45)、收盘前15分钟(14:45)。”

**注意**：执行命令前先定位到代码所在路径，`cd "$SKILL_DIR"`，并统一使用 `$PY`执行命令。

### 推送执行逻辑

运行下面命令：
```bash
cd "$SKILL_DIR"
$PY push_runtime.py init-config
$PY push_runtime.py status
$PY push_runtime.py set-feishu-target --receive-id "<RECEIVE_ID>" --receive-id-type chat_id
# 或：printf "%s" "<WEBHOOK_URL>" | $PY push_runtime.py set-feishu-webhook --url -
$PY push_runtime.py configure --active --keywords "<KEYWORD1>,<KEYWORD2>" --schedule 5m --page-size 10
$PY push_runtime.py run-once --quiet
$PY push_runtime.py run-daily-summary --quiet
```

### 安装系统级定时任务（推荐）

执行命令：

```bash
cd "$SKILL_DIR"
$PY push_runtime.py install-schedule
```
- Linux/macOS 下写入当前用户 crontab
- `install-schedule` 写入 crontab。它会同时安装事件推送和每日统计两条任务，并把 stdout/stderr 丢弃到 `/dev/null`；运行时异常会写入 `push_history.json` 的 `last_error`，可通过 `$PY push_runtime.py status` 查看。

停止自动推送：

```bash
$PY push_runtime.py uninstall-schedule
```

### 修改推送调度

当用户说"改为 15 分钟推送一次"/"每天上午 9:15 推送"等：

1. 解析用户指定的新调度档位。只能选择：`5m`（每 5 分钟）、`15m`（每 15 分钟）、`60m`（每 60 分钟）、`24h`（每 24 小时）、`daily-0915`（每天 09:15）、`daily-1245`（每天 12:45）、`daily-1445`（每天 14:45）。
2. 执行 `$PY push_runtime.py configure --schedule <SCHEDULE>` 更新 `schedule`。
3. 若当前 `active=true`，执行一次 `$PY push_runtime.py install-schedule` 使新调度立即生效。
4. 回复用户确认："已将推送调度调整为 `<对应档位>`。可选档位包括：每5分钟、每15分钟、每60分钟、每24小时、开盘前15分钟(09:15)、下午开盘前15分钟(12:45)、收盘前15分钟(14:45)。"

如果用户指定了其它间隔或时间（例如每7分钟、每30分钟、10:00、15:00 等），不要自行换算、不要写入配置。直接回复："暂不支持这个推送时间。当前只支持：每5分钟、每15分钟、每60分钟、每24小时、开盘前15分钟(09:15)、下午开盘前15分钟(12:45)、收盘前15分钟(14:45)。"

### 修改关键词

当用户说"关注半导体"/"推送关键词改为新能源"/"同时关注半导体和光模块"等：

1. 提取关键词并限制最多 3 个。超过 3 个时直接回复暂不支持超过 3 个关键词，请用户删减到 3 个以内，不要写入配置。
2. 执行 `$PY push_runtime.py configure --keywords "<KEYWORD1>,<KEYWORD2>,<KEYWORD3>"` 更新 `keywords`。单关键词也使用 `--keywords "<KEYWORD>"`。
3. 立即用新关键词执行一次推送。
4. 回复用户确认。

### 停止推送

当用户说"停止推送"/"关闭推送"等：

1. 停止自动推送代码的运行
2. **同时移除每日统计的 crontab 任务**（如果存在）。
3. 执行 `$PY push_runtime.py configure --inactive`，将 `active` 设为 `false`。
4. 回复用户确认："已停止事件推送和每日统计。随时可以说'开始推送'重新启动。"

### 运行时行为

`run-once` 执行链路：

1. 读取 `push_config.json`
2. 读取 `keywords`，对每个关键词并行调用 `search_events()` 拉取当前 `schedule` 对应时间窗口内的事件
3. 结构化格式化
4. 合并同批结果并按 `eventId` 去重，事件上保留 `matched_keywords`；再基于历史索引过滤已推送事件
5. 推送到运行时解析出的飞书目标（webhook 或自建应用接收目标）。自建应用路径会先用 App ID/App Secret 获取 `tenant_access_token`，再调用消息接口发送。
6. 写入 `push_history.json`；只有真实飞书发送成功后才更新 `sent_event_index` 和 `last_push_time`，`--dry-run` 不会把事件标记为已推送。
7. 清理超过 `retention_days` 的历史与去重索引

无新事件时：静默，不推送，只更新运行时间。

`run-daily-summary` 执行链路：

1. 读取 `push_config.json` 的当前 `keywords`
2. 调用 `daily_event_summary_many(keywords=<当前 keywords>, minutes=1440)` 实时查询过去 24 小时事件；内部按关键词并行拉明细，再按 `eventId` 全局去重
3. 每日统计必须展示各关键词分别命中的 S 级、A 级、其他等级和合计；同时展示去重后的总数和重复命中条数
4. 不从 `push_history.json` 聚合或展示历史关键词

**上下文边界**：自动任务由 crontab 在独立进程中执行，`install-schedule` 生成的命令使用 `--quiet >/dev/null 2>&1`。虽然 stdout/stderr 被丢弃，但 CLI 捕获到的异常会持久化到 `push_history.json.last_error`，`status` 会展示最近错误。

### 手动指定并立即推送事件

当用户说"查最近5小时的事件"/"最近有什么AI事件"/"搜一下半导体事件"等：

1. 解析用户指定的时间范围（如"5小时" → minutes=300），如未指定默认 60 分钟。
2. 使用当前配置的 `keywords`（或用户指定的关键词），执行一次手动推送。多关键词最多 3 个，超过 3 个直接回复不支持，不要调用命令：
   ```bash
   cd "$SKILL_DIR"
   $PY push_runtime.py manual-push --minutes <MINUTES> --keywords "<KEYWORD1>,<KEYWORD2>"
   ```
   如不推飞书，可追加 `--no-feishu`。
3. `manual-push` 会直接推送到飞书，并把本次事件 JSON 打印到 stdout，供当前上下文继续使用。
4. **同样写入 `state/push_history.json`**，确保后续可以通过"第X条详细看看"查询详情。`--dry-run` / `--no-feishu` 只记录本地批次，不更新 `sent_event_index` 和 `last_push_time`，正式推送仍会把这些事件视为未推送。
5. 只有真实飞书发送成功时，才更新 `state/push_config.json` 的 `last_push_time`。
6. **后续引导**（追加在事件列表末尾）：
   - 有事件时：
     ```
     💡 对某条感兴趣？直接说「第X条详细看看」即可查看完整分析。
     🔍 想看其他时间段？说「查最近1小时」或「查最近24小时」。
     🔄 想换个主题？说「搜一下半导体事件」。
     ```
   - 无事件时：
     ```
     💡 最近 N 分钟/小时暂无新事件。想扩大范围？说「查最近24小时」。
     🔄 想换个主题？说「搜一下半导体事件」。
     ```

## 第二部分：事件详情查询

### 触发方式

用户可以通过以下任意方式触发详情查询：

| 用户说法示例                     | 匹配方式     |
|----------------------------------|-------------|
| "第3条详细看看"                  | 按序号匹配   |
| "那个关于芯片的事件看一下"         | 按关键词模糊匹配标题/摘要 |
| "长飞光纤那条"                   | 按标题中的实体名称匹配 |
| "上一轮推送的第1条"               | 指定历史批次 |

> **注意**：eventId 对用户不可见，用户不会通过 eventId 来指定事件。eventId 仅在内部用于调用详情 API。

### 执行逻辑

#### 步骤 1：定位 eventId

1. **优先使用当前上下文**：如果本轮或上一轮刚展示过事件列表，用户说"第X条"时直接以那份刚展示的列表为准，不重新推断"最新批次"。
2. **读取推送历史**：当前上下文没有可用事件列表时，读取 `state/push_history.json`。
3. **最新批次定义**：`batches` 数组顺序是唯一权威顺序，`batches[0]` 就是最新批次；不要按 `push_time`、`last_push_time` 或写入时间重新排序。
4. **根据用户意图匹配事件**：
   - **按序号**（如"第3条"/"第三条详细看看"）：取当前上下文列表或 `batches[0]` 中 `index == 3` 的事件。
   - **按标题/摘要关键词**（如"关于芯片的"/"长飞光纤那条"）：在当前上下文列表或 `batches[0]` 的 `compliantTitle` 、`summary`和 `original_summary` 中做模糊匹配。如果匹配到多条，列出候选（带序号）让用户选择。
   - **指定历史批次**（如"上一轮第2条"）：在 `batches[1]`（上一轮）中按序号查找。
   - **长时历史批次**（如"上午的某一条"）：在所有历史记录batch中的 `compliantTitle` 、`summary`和 `original_summary` 中做模糊匹配。如果匹配到多条，列出候选让用户选择。
5. **如果找不到**：提示用户"未在最近的推送记录中找到该事件"，并列出最近一批事件的标题供用户确认。
6. **提取 eventId 和对应的 `matched_keywords`**（每个 batch 记录了当时的 `keywords`，事件自身记录 `matched_keywords`，eventId 在缓存中但不展示给用户）。

#### 步骤 2：调用详情接口

```bash
cd "$SKILL_DIR"
$PY -c "import json; from event_query import get_event_detail; print(json.dumps(get_event_detail(keyword='AI', event_id='76679'), ensure_ascii=False, indent=2))"
```

#### 步骤 3：格式化输出 — 结构化事件分析报告

将 API 返回的原始 JSON 组织成以下专业分析报告。报告分为 **8 个板块**，按固定顺序输出，每个板块之间用分隔线隔开。如果某个字段为空或 null，该板块标注"暂无数据"，不省略板块本身。

**飞书发送渲染要求**：如果需要把本详细报告发送到飞书，必须通过飞书的富文本/卡片接口发送（如 interactive card、post/rich text 等能够渲染 Markdown 或表格的消息类型），不要使用普通文本消息接口或纯文本 `message` 工具发送。第七部分“核心标的”依赖 Markdown 表格渲染；若走纯文本通道，飞书会直接显示 `| 标的名称 | 代码 | ... |` 原始符号，视为发送方式错误。

---

**完整报告模板：**

> **格式说明**：以下模板为理想输出格式。若 LLM 无法精确对齐 box-drawing 字符（如虚线卡片框、方框传导链路），请使用各板块中标注的简化格式，优先保证内容完整和可读性。

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📋  事 件 深 度 分 析 报 告
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  📰  compliantTitle
  🕐  eventPublishDate
  🔶  信号等级: signalLevel ｜ 信号类型: signalCategory



═══════════════════════════════════════════════
  📝  一、核 心 摘 要
═══════════════════════════════════════════════


  original_summary 的完整内容



═══════════════════════════════════════════════
  🔗  二、投 资 逻 辑 与 多 空 博 弈
═══════════════════════════════════════════════


  ◆ 核心逻辑链路

    （从 investmentLogic 中提取"核心逻辑："后的内容）

  ◆ 分支逻辑

    · AI多模态布局:
      持股PixVerse → 影视大模型领先 → 商业化落地 → 股权增值

    · 估值重估逻辑:
      PB低于行业 → AI资产未充分定价 → 重估空间明确

    （每条分支单独一段，链路用 → 串联，换行缩进保持整洁）

  ◆ 多空博弈

    🔴 红方 59%  vs  🔵 蓝方 41%  ── 红方微弱占优

    （从 investmentLogic 中提取红蓝方胜率）



═══════════════════════════════════════════════
  🌳  三、推 理 验 证 链 路
═══════════════════════════════════════════════


  ① 步骤1的结论
       ↓
  ② 步骤2的结论
       ↓
  ③ 步骤3的结论
       ↓
  ④ ...
       ↓
  ⑤ 决策综合

  （数据来自 overallReasoningChain，是 JSON 字符串数组，
    解析后逐步展示，用带编号的纵向箭头链串联，每步独占一行）



═══════════════════════════════════════════════
  🏗️  四、产 业 链 全 景
═══════════════════════════════════════════════


  （直接输出 formatted_tree 的完整内容，保留原始缩进和层级结构，
    除此之外不要自己生成其他内容）



═══════════════════════════════════════════════
  ⚡  五、传 导 路 径 与 节 奏
═══════════════════════════════════════════════


  ▸ 触发事件:  transmission_logic.trigger

  ▸ 传导链路:

    节点A ──(周期/B级)──▸ 节点B ──(周期/B级)──▸ 节点C

    （数据来自 transmission_logic.steps 数组，
      每个 step 包含 node / cycle / grade。
      默认使用上方简化箭头格式，LLM 可稳定复现。
      若输出环境支持等宽字符且能精确对齐，可选用方框增强版：

      ┌─────────┐   周期    ┌─────────┐   周期    ┌─────────┐
      │  节点A  │ ────────▸ │  节点B  │ ────────▸ │  节点C  │
      │  B级    │           │  B级    │           │  C级    │
      └─────────┘           └─────────┘           └─────────┘
    ）

  ▸ 总传导周期:  transmission_logic.total_cycle



═══════════════════════════════════════════════
  📚  六、逻 辑 库 匹 配
═══════════════════════════════════════════════


  对本事件提取的可复用投资逻辑模式：

  ── ▸ 逻辑模式 1:  logic_name ──────────────

    📎 标准链路:
       standard_chain
       （如果链路较长，按 → 分段换行展示）

    📊 置信度:   ████████░░  confidence_score × 100%

    📈 历史成功率: ██████░░░░  estimated_success_rate × 100%
       （用进度条可视化：每10%一个█，不足部分用░，总长度固定10格）

    🔍 信号特征:
       signal_characteristics
       （如果是一大段文本，按句号/分号拆分为要点列表：
         · 特征要点 1
         · 特征要点 2
         · 特征要点 3
       ）

    🎯 适用场景:
       · applicable_scenarios[0]
       · applicable_scenarios[1]

    ⚠️ 风险打破条件:
       · risk_breakers[0]
       · risk_breakers[1]


  ── ▸ 逻辑模式 2:  ... ────────────────────
    （同上格式）

  （默认使用上方 ── ▸ 标题 ── 分隔线格式，LLM 可稳定复现。
    若输出环境支持等宽字符，可选用虚线卡片框增强视觉效果：
    ┌ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┐
      ▸ 逻辑模式 N:  名称
    └ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┘
  ）


  ──────────────────────────────────────
  📋 综合评估:
     extraction_summary
     （如果是长段落，按语义拆分为 2-3 个要点）
  ──────────────────────────────────────

  （数据来自 logic_library_output.extracted_patterns 数组）



═══════════════════════════════════════════════
  🎯  七、核 心 标 的
═══════════════════════════════════════════════


  | 标的名称 | 代码 | 相关度 | 研究观点 |
  |---------|------|-------|---------|
  | 电广传媒 | 000917.SZ | 高 | 中性偏谨慎 |
  | ...     | ...  | ...   | ...     |

  （数据来自 investmentTargetsSummary 数组，每行一个标的。
    必须使用 Markdown 表格格式输出，不要用空格对齐。）



═══════════════════════════════════════════════
  ⚠️  八、关 键 风 险
═══════════════════════════════════════════════


  🔸 1. keyRisks[0]

  🔸 2. keyRisks[1]

  🔸 3. ...

  （每条风险独占一段，风险之间留空行，便于阅读）



═══════════════════════════════════════════════
  🔬  附 录：历 史 案 例 参 考
═══════════════════════════════════════════════


  historical_cases_analysis 的完整内容
  （如果内容是长段落，按案例/时间点拆分为独立段落，
    每个案例之间留空行）


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**字段到板块的完整映射：**

| 板块                  | 数据来源字段                  | 处理说明                                        |
|-----------------------|-------------------------------|-------------------------------------------------|
| 基本信息（报告头）     | `compliantTitle`, `eventPublishDate`, `signalLevel`, `signalCategory` | 直接输出                       |
| 一、核心摘要           | `original_summary`            | 完整输出，不截断                                |
| 二、投资逻辑与多空博弈 | `investmentLogic`             | 拆分为核心逻辑、分支逻辑、多空博弈三段           |
| 三、推理验证链路       | `overallReasoningChain`       | JSON 字符串，需先 `json.loads()` 解析为数组      |
| 四、产业链全景         | `formatted_tree`              | 原样输出，保留树形缩进                           |
| 五、传导路径与节奏     | `transmission_logic`          | 对象，含 `trigger`, `steps[]`, `total_cycle`     |
| 六、逻辑库匹配         | `logic_library_output`        | 含 `extracted_patterns[]` 和 `extraction_summary` |
| 七、核心标的           | `investmentTargetsSummary`    | 数组，每项取 `target_name`, `target_code`, `relevance`, `research_opinion`，必须用 Markdown 表格输出 |
| 八、关键风险           | `keyRisks`                    | 字符串数组，编号列出                             |
| 附录：历史案例参考     | `historical_cases_analysis`   | 完整输出                                         |

**格式规则：**

- 使用 `━` 作报告外边框，`═` 作板块标题分隔线，视觉层次分明
- 板块标题用**中文序号 + 间隔空格**（如"一、核 心 摘 要"），字间加空格使标题更醒目
- 板块标题与内容之间**留两个空行**，内容与下一个板块之间**留三个空行**，保证呼吸感
- 数值型字段（置信度、成功率）转为百分比显示，并用 `█░` 进度条可视化（总长度固定 10 格，每格代表 10%）
- `investmentLogic` 是一段长文本，需要智能拆分为核心逻辑、分支链路、多空博弈三个子段落
- `overallReasoningChain` 是 JSON 字符串格式的数组，必须先解析再用**纵向带编号箭头链**展示
- **长段落拆分规则**：任何超过 3 句话的连续段落，都应按语义拆分为要点列表（用 `·` 前缀），每个要点独占一行。这尤其适用于第六部分的 `signal_characteristics`、`extraction_summary`，以及附录的 `historical_cases_analysis`
- 第六部分每个逻辑模式默认用 `── ▸ 标题 ──` 分隔线标识，保证 LLM 稳定复现；若输出环境支持等宽字符对齐，可选用虚线框 `┌ ─ ─ ┐` / `└ ─ ─ ┘` 增强卡片效果

#### 步骤 4：后续引导

报告输出后追加引导：
- "对报告中的某个板块想深入讨论？直接说'第三部分投资逻辑展开说说'即可。"
- "还想看其他推送事件？告诉我序号或标题关键词。"

---

## 配置文件格式

`state/push_config.json` 结构：

```json
{
  "active": false,
  "schedule": "5m",
  "keywords": ["AI"],
  "page_size": 10,
  "last_push_time": "",
  "daily_summary_time": "09:00",
  "feishu_webhooks": [],
  "feishu_receive_id": "",
  "feishu_receive_id_type": "chat_id",
  "event_intel_api_key": ""
}
```

| 字段                    | 类型    | 说明                                              |
|-------------------------|---------|---------------------------------------------------|
| `active`                | bool    | 推送是否正在运行                                   |
| `schedule`              | string  | 推送调度档位：`5m`、`15m`、`60m`、`24h`、`daily-0915`、`daily-1245`、`daily-1445` |
| `keywords`              | array   | 实际用于检索的关键词列表，默认 `["AI"]`，最多 3 个；超过 3 个直接不支持 |
| `page_size`             | int     | 每次推送返回的事件条数，默认 10                     |
| `last_push_time`        | string  | 上次真实飞书推送时间 (ISO 格式)，初始为空字符串     |
| `daily_summary_time`    | string  | 每日统计的 crontab 时间，默认 `09:00` |
| `feishu_webhooks`       | array   | 部署时配置的飞书 Webhook URL 列表；回复和日志中不要展示明文 |
| `feishu_receive_id`     | string  | 部署时配置的飞书自建应用接收目标；群聊通常为 chat_id/receive_id |
| `feishu_receive_id_type`| string  | 飞书接收目标类型，默认 `chat_id` |
| `event_intel_api_key`   | string  | deepseekdata API key fallback；优先使用内部配置，回复和日志中不要展示明文 |

---

## 触发关键词

以下关键词/意图会触发本技能：

**启动推送**：开始推送、启动推送、事件推送、定时推送、开始监控事件、打开推送等

**手动查询**：查最近X小时/分钟事件、最近有什么事件、查事件、搜事件、看看事件、有什么新事件等

**修改参数**：推送间隔、改为X分钟、推送频率、关注XX（修改关键词）等

**停止推送**：停止推送、关闭推送、暂停推送、取消推送等

**查看详情**：第X条详细看看、详情+序号、那个关于XX的事件、XX那条、上一轮第X条等

---

## 总规则

- **时区统一为北京时间（UTC+8）**：所有写入配置文件和推送历史的时间戳必须使用北京时间，格式示例 `2026-04-09T14:01:30+08:00`。严禁使用 UTC 时间或带 `Z` 后缀的时间戳。`datetime.now()` 在系统时区为 `Asia/Shanghai` 或 `Asia/Beijing` 时已返回北京时间，直接使用即可。
- 全程使用中文输出。
- **定时推送和每日统计在无新事件时必须静默跳过**，不向用户发送任何消息（包括"暂无新事件"提示）。只有手动查询才会展示"暂无新事件"的提示。
- 推送内容保持简洁，突出标题、信号等级和摘要，方便用户快速浏览。
- 详情查询时完整展示所有返回字段，需要映射成中文呈现，帮助用户做深入研判。
- 如果 API 调用失败，直接告知用户"事件数据获取失败"及错误原因，不编造数据。
- 间隔修改等用户偏好必须持久化到 `state/push_config.json`，不依赖会话记忆。
- 同时在 `memory/YYYY-MM-DD.md` 中记录重要的配置变更，便于跨会话回溯。

## 执行策略

### 用户说“开始推送 / 开启事件监控”

1. 若配置不存在，先执行 `$PY push_runtime.py init-config`。
2. 执行 `$PY push_runtime.py status` 检查密钥状态。若 `event_api.has_key=false`，请用户提供 deepseekdata API key，并通过 stdin 或 `--key-file` 保存；若飞书没有 webhook 且自建应用缺少 App ID/App Secret/receive_id 任一项，请用户提供缺失的飞书凭据或接收目标（Webhook URL，或自建应用 App ID/App Secret/receive_id），但对外不要提及内部配置文件名。拿到 receive_id 后执行 `$PY push_runtime.py set-feishu-target --receive-id "<RECEIVE_ID>" --receive-id-type chat_id`；拿到 Webhook URL 后通过 stdin 或 `--url-file` 保存。
3. 确认运行时已解析到飞书发送目标；执行 `$PY push_runtime.py configure --active --keywords "<KEYWORD1>,<KEYWORD2>" --schedule <SCHEDULE> --page-size <PAGE_SIZE>` 写入关键词、调度档位、条数，并设置 `active=true`。单关键词也使用 `--keywords "<KEYWORD>"`。多关键词最多 3 个，超过 3 个直接回复不支持，不要调用命令。每日统计与事件推送一起启动。
4. 执行 `$PY push_runtime.py run-once --quiet`（立即验证一次推送链路，但不把事件 JSON 放入当前上下文）。
5. 执行 `$PY push_runtime.py install-schedule`。
6. 执行 `$PY push_runtime.py status` 并回告用户：“已交给系统定时任务执行，不再依赖会话轮询。当前按 `<当前档位>` 推送。如果有其他需要，可以改成：每5分钟、每15分钟、每60分钟、每24小时、开盘前15分钟(09:15)、下午开盘前15分钟(12:45)、收盘前15分钟(14:45)。”
7. 不要创建或启用Gateway cron / `jobs.json` / `agentTurn` / `announce` 任务；如果发现旧任务存在，应说明它们是另一套机制，建议停用后只保留系统 crontab。

### 用户说“改成每15分钟 / 改关键词 / 改推送飞书”

1. 执行 `$PY push_runtime.py configure` 更新关键词、调度档位、条数或启停状态。
2. 若当前 `active=true`，重新执行 `$PY push_runtime.py install-schedule`。
3. 如果用户指定的是支持档位，反馈：“已将推送调度调整为 `<对应档位>`，重新安装调度后生效。”如果用户指定了其它值，反馈：“暂不支持这个推送时间。当前只支持：每5分钟、每15分钟、每60分钟、每24小时、开盘前15分钟(09:15)、下午开盘前15分钟(12:45)、收盘前15分钟(14:45)。”

### 用户说“查最近5小时 / 手动推送一次”

1. 解析时间窗口、关键词和条数。
2. 执行 `$PY push_runtime.py manual-push --minutes <MINUTES> --keywords "<KEYWORD1>,<KEYWORD2>"`；单关键词也使用 `--keywords "<KEYWORD>"`。多关键词最多 3 个，超过 3 个直接回复不支持。
3. 将命令 stdout 中的事件列表用于当前上下文回复；历史写入和飞书推送由 `manual-push` 完成。

### 用户说“停止推送”

1. 执行 `$PY push_runtime.py uninstall-schedule`
2. 运行时会同时移除事件推送和每日统计 crontab，并将 `active` 改为 `false`；也可以执行 `$PY push_runtime.py configure --inactive` 确认状态关闭。
3. 回告已停止

### 用户说“看某条详情”

1. 先执行 `$PY push_runtime.py detail-from-ref --query "<用户原话>"`。这个命令会解析“第X条详细看看”“详细看看第X个”“我要看第X个的深度报告”“XX那条”等问法，并优先从 `state/push_history.json` 的最新批次 `batches[0]` 匹配事件；“上一轮/上一次”类说法会匹配 `batches[1]`。
2. 如果命令返回 `status=ok`，必须使用返回的 `detail` 字段生成“结构化事件分析报告”，并明确数据来源为 `deepseekdata API`。不要改用网页搜索或公开新闻摘要。
3. 如果命令返回 `status=ambiguous`，列出 `candidates` 的序号和标题，请用户补充选择，不要自行猜测。
4. 如果命令返回 `status=not_found`，先明确说明“该事件不在最近推送事件范围内，无法使用 deepseekdata 推送事件详情接口命中；以下报告将改用其他数据源生成”，然后再使用其他可用数据源生成报告。
5. 如果命令返回 API key 或网络错误，直接告知“事件数据获取失败”及错误原因，不编造 deepseekdata 详情。

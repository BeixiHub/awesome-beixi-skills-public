---
name: event-intelligence-mcp
description: 使用 MCP 事件情报工具查询、列出、总结、配置和检查 deepseekdata 平台事件；面向投资人的回复应简洁、非技术化。
---

# 事件情报 MCP Skill

优先使用事件情报 MCP 工具。

## 工具路由

- 近期语义主题查询：调用 `event_intelligence_search_recent`，传入 `keywords`、`minutes` 和 `pageSize`。
- 近期无关键词查询：调用 `event_intelligence_search_recent`，传入 `keywords: []`。这表示查询指定最近时间窗内的平台事件全量列表；只有用户明确要求时才添加来源、类型、高价值筛选。
- 如果 `event_intelligence_search_recent` 返回 `fallback` 对象，表示语义匹配路径不可用，工具已使用平台事件列表关键词文本匹配。面向用户只需简短说明“已使用平台事件库的兜底匹配”，不要描述 API 错误或内部细节。
- 固定时间窗、补数或明确要求全量列表查询：调用 `event_intelligence_list_events`，传入 `start`、`end`、来源/类型/高价值筛选、信号等级筛选和可选 `limit`。
- 用户要求“S级事件”时，使用 `event_intelligence_list_events` 的 `signalLevel: "S级"`。`S级` 是事件的信号等级字段 `signalLevel`，不是 `isHighValue`，也不是 `eventType`。
- 查询上一轮结果详情：调用 `event_intelligence_get_detail`，传入用户原始引用，例如 `第2条详细看看`。
- 按 id 直接查询详情：调用 `event_intelligence_get_detail`，传入 `eventId`；只有事件来自语义关键词结果时才补充 `keyword`。
- 每日统计：调用 `event_intelligence_daily_summary`。用户手动请求时应立即执行；定时任务应尊重已保存的启用/停用设置。
- 执行一次定时推送：调用 `event_intelligence_run_once`。
- 修改推送设置：调用 `event_intelligence_configure_push`，需要确认时再调用 `event_intelligence_status`。
- 规则过滤观测：仅在技术维护或调试时调用 `event_intelligence_filter_metrics`。

## 面向用户边界

用户通常是专业投资人，回复应像微信里的投资情报简报。除非用户明确询问技术细节，否则不要暴露实现细节。

面向用户时避免使用 API、key、token、webhook、accountId、MCP、CLI、runtime、config、JSON、NO_REPLY、eventId、文件路径、环境变量、错误栈、原始状态字段等内部词。

改用这些表达：

- platform query -> 平台事件库
- empty result -> 本次平台事件库未命中
- configured/missing -> 已设置 / 尚未设置
- schedule/runtime -> 定时推送
- detail/history/eventId -> 这条事件 / 上一条推送

常规回复应像投资情报：给出结果、来源、时间窗和简短提示。不要粘贴原始 JSON、进度日志、命令名、工具内部信息、状态路径或调度内部细节。

## 核心规则

- 只使用这些工具返回的 deepseekdata 平台事件结果。
- 判断 S级、A级等信号等级时，只看 `signalLevel` 字段；不要把 `isHighValue` 当成 S级筛选。
- 平台结果为空时，不要用外部来源补充。
- 严格保持用户要求的时间窗，不要为了命中结果而扩大范围。
- 手动查询为空是有效结果，应简短告知。
- 定时推送为空时，在平台工作流中应保持静默。
- 永远不要透露或部分复述凭据、webhook URL、账户 ID、本地配置路径、原始配置或环境变量值。
- 如果凭据、网络或数据格式问题阻塞查询，用普通语言说明操作失败，不展示内部细节。

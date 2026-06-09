"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { spawn } = require("node:child_process");

const PLUGIN_DIR = path.resolve(__dirname, "..");
const SCRIPT_DIR = path.join(PLUGIN_DIR, "scripts");
const PUSH_RUNTIME = path.join(SCRIPT_DIR, "push_runtime.py");
const TOOL_BRIDGE = path.join(SCRIPT_DIR, "tool_bridge.py");
const PYTHON = process.env.EVENT_INTEL_PYTHON || process.env.PYTHON || "python3";

function redactSensitiveText(value) {
  return String(value || "")
    .replace(/sk-[A-Za-z0-9._-]{6,}/g, "<redacted>")
    .replace(/(?:EVENT_INTEL_API_KEY|DEEPSEEKDATA_API_KEY)\s*=\s*['"]?[^'"\s,;]+/g, "<redacted>")
    .replace(/(event_intel_api_key|deepseekdata_api_key|api_key|apiKey)\s*[:=]\s*['"]?[^'"\s,;}]+/gi, "$1=<redacted>")
    .replace(/https:\/\/open\.feishu\.cn\/open-apis\/bot\/v2\/hook\/[A-Za-z0-9._-]+/g, "<redacted>");
}

function runPython(args, options = {}) {
  const inputJson = options.inputJson;
  return new Promise((resolve, reject) => {
    const child = spawn(PYTHON, args, {
      cwd: SCRIPT_DIR,
      env: {
        ...process.env,
        PYTHONIOENCODING: "utf-8",
      },
      stdio: ["pipe", "pipe", "pipe"],
    });

    let stdout = "";
    let stderr = "";
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk) => {
      stdout += chunk;
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk;
    });
    child.on("error", (error) => {
      reject(error);
    });
    child.on("close", (code) => {
      const out = stdout.trim();
      let parsed = null;
      if (out) {
        try {
          parsed = JSON.parse(out);
        } catch (error) {
          reject(new Error(`工具运行时返回了非 JSON 输出：${redactSensitiveText(out).slice(0, 800)}`));
          return;
        }
      }

      if (code !== 0) {
        if (parsed) {
          resolve(parsed);
          return;
        }
        reject(new Error(redactSensitiveText(stderr || stdout || `工具运行时退出，退出码：${code}`)));
        return;
      }
      resolve(parsed || { status: "ok" });
    });

    if (inputJson !== undefined) {
      child.stdin.write(JSON.stringify(inputJson));
    }
    child.stdin.end();
  });
}

function runPushRuntime(args) {
  return runPython([PUSH_RUNTIME, ...args]);
}

function runBridge(command, payload) {
  return runPython([TOOL_BRIDGE, command], { inputJson: payload || {} });
}

function hasText(value) {
  return value !== undefined && value !== null && String(value).trim() !== "";
}

function clampInteger(value, min, max, fallback) {
  const parsed = Number.parseInt(value, 10);
  if (!Number.isFinite(parsed)) {
    return fallback;
  }
  return Math.max(min, Math.min(max, parsed));
}

function normalizeKeywords(value) {
  if (value === undefined) {
    return undefined;
  }
  if (value === null) {
    return "";
  }
  if (Array.isArray(value)) {
    return value.map((item) => String(item || "").trim()).filter(Boolean).join(",");
  }
  return String(value || "").trim();
}

function addValue(args, flag, value, options = {}) {
  const allowEmpty = options.allowEmpty === true;
  if (value === undefined || value === null) {
    return;
  }
  const text = String(value);
  if (!allowEmpty && text.trim() === "") {
    return;
  }
  args.push(flag, text);
}

function addSwitch(args, flag, enabled) {
  if (enabled) {
    args.push(flag);
  }
}

const schemas = {
  searchRecent: {
    type: "object",
    properties: {
      keywords: {
        oneOf: [
          { type: "array", items: { type: "string" }, maxItems: 3 },
          { type: "string" }
        ],
        description: "语义主题或关键词。传空数组或空字符串表示无关键词的近期全量事件查询。"
      },
      minutes: { type: "integer", minimum: 1, maximum: 10080, default: 60, description: "查询最近多少分钟的事件。" },
      pageSize: { type: "integer", minimum: 1, maximum: 30, default: 10, description: "最多返回多少条事件。" },
      eventSource: { type: "string", description: "无关键词全量查询时可选的事件来源筛选。" },
      eventType: { type: "string", description: "无关键词全量查询时可选的事件类型筛选。" },
      isHighValue: {
        oneOf: [{ type: "boolean" }, { type: "string" }],
        description: "无关键词全量查询时可选的高价值事件筛选。"
      },
      noDelivery: {
        type: "boolean",
        default: true,
        description: "记录查询历史以便后续查详情，但不标记为已推送。"
      },
      dryRun: { type: "boolean", default: false, description: "为 true 时仅试运行，不写入正式推送状态。" }
    },
    additionalProperties: false
  },
  listEvents: {
    type: "object",
    properties: {
      start: { type: "string", description: "北京时间起始时间，格式 YYYY-MM-DD HH:MM:SS。需与 end 同时提供。" },
      end: { type: "string", description: "北京时间结束时间，格式 YYYY-MM-DD HH:MM:SS。需与 start 同时提供。" },
      minutes: { type: "integer", minimum: 1, maximum: 10080, default: 60, description: "未提供 start/end 时，查询最近多少分钟的事件。" },
      eventSource: { type: "string", description: "可选的事件来源筛选。" },
      eventType: { type: "string", description: "可选的事件类型筛选。" },
      isHighValue: { oneOf: [{ type: "boolean" }, { type: "string" }], description: "可选的高价值事件筛选。" },
      signalLevel: {
        type: "string",
        enum: ["S级", "A级", "B级", "C级", "S", "A", "B", "C"],
        description: "可选的信号等级筛选。查询“S级事件”时应传 \"S级\"；不要使用 isHighValue 代替。"
      },
      pageSize: { type: "integer", minimum: 1, maximum: 100, default: 100, description: "分页大小，最大 100。" },
      limit: { type: "integer", minimum: 0, default: 0, description: "最多保留多少条结果；0 表示按接口总量全量拉取。" },
      noDelivery: { type: "boolean", default: true, description: "记录查询历史以便后续查详情，但不标记为已推送。" }
    },
    additionalProperties: false
  },
  getDetail: {
    type: "object",
    properties: {
      query: {
        type: "string",
        description: "用户对上一轮结果的引用，例如“第2条详细看看”。近期搜索或列表查询后优先使用。"
      },
      eventId: { type: "string", description: "直接按事件 id 查询详情。语义详情查询需配合 keyword；无关键词全量列表详情查询可省略 keyword。" },
      keyword: { type: "string", description: "与 eventId 配合使用的语义关键词。" },
      start: { type: "string", description: "可选的全量列表详情查询起始时间，北京时间。" },
      end: { type: "string", description: "可选的全量列表详情查询结束时间，北京时间。" },
      eventSource: { type: "string", description: "可选的事件来源筛选，用于缩小详情回查范围。" },
      eventType: { type: "string", description: "可选的事件类型筛选，用于缩小详情回查范围。" },
      isHighValue: { oneOf: [{ type: "boolean" }, { type: "string" }], description: "可选的高价值事件筛选，用于缩小详情回查范围。" }
    },
    additionalProperties: false
  },
  dailySummary: {
    type: "object",
    properties: {
      respectActiveSetting: {
        type: "boolean",
        default: false,
        description: "为 true 时，如果已保存的推送设置为停用则跳过。用户手动请求通常保持 false。"
      },
      dryRun: { type: "boolean", default: false, description: "为 true 时仅试运行，不写入正式推送状态。" }
    },
    additionalProperties: false
  },
  runOnce: {
    type: "object",
    properties: {
      runWhenInactive: {
        type: "boolean",
        default: false,
        description: "为 true 时，即使已保存的推送设置为停用，也执行一次推送周期。"
      },
      dryRun: { type: "boolean", default: false, description: "为 true 时仅试运行，不写入正式推送状态。" }
    },
    additionalProperties: false
  },
  configurePush: {
    type: "object",
    properties: {
      active: { type: "boolean", description: "是否启用事件情报定时推送。" },
      keywords: {
        oneOf: [
          { type: "array", items: { type: "string" }, maxItems: 3 },
          { type: "string" }
        ],
        description: "定时推送使用的语义主题或关键词；传空数组或空字符串表示无关键词全量事件。"
      },
      eventSource: { type: "string", description: "无关键词推送时使用的事件来源筛选。" },
      eventType: { type: "string", description: "无关键词推送时使用的事件类型筛选。" },
      isHighValue: { oneOf: [{ type: "boolean" }, { type: "string" }], description: "无关键词推送时使用的高价值事件筛选。" },
      schedule: {
        type: "string",
        enum: ["5m", "15m", "60m", "24h", "daily-0915", "daily-1245", "daily-1445"],
        description: "定时推送频率或固定推送时间。"
      },
      pageSize: { type: "integer", minimum: 1, maximum: 30, description: "每次推送最多保留多少条事件。" },
      dailySummaryTime: { type: "string", pattern: "^\\d{1,2}:\\d{2}$", description: "每日统计推送时间，格式 HH:MM。" },
      filterObservability: { type: "string", enum: ["off", "metrics", "debug"], description: "规则过滤观测级别：off 不写入，metrics 写聚合指标，debug 写候选链路。" }
    },
    additionalProperties: false
  },
  status: {
    type: "object",
    properties: {},
    additionalProperties: false
  },
  filterMetrics: {
    type: "object",
    properties: {
      hours: { type: "integer", minimum: 1, maximum: 720, default: 24, description: "统计最近多少小时的观测指标。" },
      days: { type: "integer", minimum: 0, maximum: 30, default: 0, description: "统计最近多少天的观测指标；提供后会覆盖 hours。" },
      limitReasons: { type: "integer", minimum: 1, maximum: 100, default: 20, description: "最多返回多少个过滤原因。" }
    },
    additionalProperties: false
  }
};

const tools = [
  {
    name: "event_intelligence_search_recent",
    description: "按语义关键词查询近期平台事件；当 keywords 为空时，查询近期全量平台事件。",
    parameters: schemas.searchRecent,
    handler: async (input = {}) => {
      const args = [
        "manual-push",
        "--minutes", String(clampInteger(input.minutes, 1, 10080, 60)),
        "--page-size", String(clampInteger(input.pageSize, 1, 30, 10)),
      ];
      const keywords = normalizeKeywords(input.keywords);
      const hasStructuredFilters = hasText(input.eventSource) || hasText(input.eventType) || input.isHighValue !== undefined;
      if (keywords !== undefined) {
        addValue(args, "--keywords", keywords, { allowEmpty: true });
      } else if (hasStructuredFilters) {
        addValue(args, "--keywords", "", { allowEmpty: true });
      }
      addValue(args, "--event-source", input.eventSource);
      addValue(args, "--event-type", input.eventType);
      if (input.isHighValue !== undefined) {
        addValue(args, "--is-high-value", input.isHighValue);
      }
      addSwitch(args, "--dry-run", input.dryRun === true);
      addSwitch(args, "--no-delivery", input.noDelivery !== false);
      return runPushRuntime(args);
    }
  },
  {
    name: "event_intelligence_list_events",
    description: "按时间窗、事件来源、事件类型、高价值标记和信号等级查询平台事件。S级事件应使用 signalLevel: \"S级\" 筛选。",
    parameters: schemas.listEvents,
    handler: async (input = {}) => {
      const args = ["list-events"];
      addValue(args, "--start", input.start);
      addValue(args, "--end", input.end);
      addValue(args, "--minutes", clampInteger(input.minutes, 1, 10080, 60));
      addValue(args, "--event-source", input.eventSource);
      addValue(args, "--event-type", input.eventType);
      if (input.isHighValue !== undefined) {
        addValue(args, "--is-high-value", input.isHighValue);
      }
      addValue(args, "--signal-level", input.signalLevel);
      addValue(args, "--page-size", clampInteger(input.pageSize, 1, 100, 100));
      addValue(args, "--limit", clampInteger(input.limit, 0, 10000, 0));
      addSwitch(args, "--no-delivery", input.noDelivery !== false);
      return runPushRuntime(args);
    }
  },
  {
    name: "event_intelligence_get_detail",
    description: "从近期查询历史或按事件 id 获取单条事件的完整分析。",
    parameters: schemas.getDetail,
    handler: async (input = {}) => {
      if (hasText(input.query)) {
        return runPushRuntime(["detail-from-ref", "--query", String(input.query)]);
      }
      if (!hasText(input.eventId)) {
        return {
          status: "error",
          detail: "必须提供 query 或 eventId。"
        };
      }
      if (hasText(input.keyword)) {
        return runBridge("semantic-detail", {
          eventId: input.eventId,
          keyword: input.keyword
        });
      }
      return runBridge("structured-detail", {
        eventId: input.eventId,
        start: input.start || "",
        end: input.end || "",
        eventSource: input.eventSource || "",
        eventType: input.eventType || "",
        isHighValue: input.isHighValue
      });
    }
  },
  {
    name: "event_intelligence_daily_summary",
    description: "基于已配置主题或无关键词筛选，生成过去 24 小时平台事件等级统计。",
    parameters: schemas.dailySummary,
    handler: async (input = {}) => {
      const args = ["run-daily-summary"];
      const respectActiveSetting = input.respectActiveSetting === true || input.force === false;
      addSwitch(args, "--force", !respectActiveSetting);
      addSwitch(args, "--dry-run", input.dryRun === true);
      return runPushRuntime(args);
    }
  },
  {
    name: "event_intelligence_run_once",
    description: "执行一次已配置的定时推送周期，并根据近期推送历史去重。",
    parameters: schemas.runOnce,
    handler: async (input = {}) => {
      const args = ["run-once"];
      addSwitch(args, "--force", input.runWhenInactive === true || input.force === true);
      addSwitch(args, "--dry-run", input.dryRun === true);
      return runPushRuntime(args);
    }
  },
  {
    name: "event_intelligence_configure_push",
    description: "更新事件情报推送配置，不暴露凭据。",
    parameters: schemas.configurePush,
    handler: async (input = {}) => {
      const args = ["configure"];
      if (input.active === true) {
        args.push("--active");
      } else if (input.active === false) {
        args.push("--inactive");
      }
      const keywords = normalizeKeywords(input.keywords);
      if (keywords !== undefined) {
        addValue(args, "--keywords", keywords, { allowEmpty: true });
      }
      addValue(args, "--event-source", input.eventSource);
      addValue(args, "--event-type", input.eventType);
      if (input.isHighValue !== undefined) {
        addValue(args, "--is-high-value", input.isHighValue);
      }
      addValue(args, "--schedule", input.schedule);
      addValue(args, "--page-size", input.pageSize);
      addValue(args, "--daily-summary-time", input.dailySummaryTime);
      addValue(args, "--filter-observability", input.filterObservability);
      return runPushRuntime(args);
    }
  },
  {
    name: "event_intelligence_status",
    description: "返回已脱敏的运行状态、筛选配置、推送计划和近期历史摘要。",
    parameters: schemas.status,
    handler: async () => runPushRuntime(["status"])
  },
  {
    name: "event_intelligence_filter_metrics",
    description: "维护工具：在规则过滤观测开启时查看聚合指标。",
    parameters: schemas.filterMetrics,
    handler: async (input = {}) => {
      const args = ["metrics-summary"];
      addValue(args, "--hours", clampInteger(input.hours, 1, 720, 24));
      addValue(args, "--days", clampInteger(input.days, 0, 30, 0));
      addValue(args, "--limit-reasons", clampInteger(input.limitReasons, 1, 100, 20));
      return runPushRuntime(args);
    }
  }
];

function publicTool(tool) {
  return {
    name: tool.name,
    description: tool.description,
    parameters: tool.parameters
  };
}

function getTools() {
  return tools.map(publicTool);
}

async function callTool(name, args = {}, context = {}) {
  const tool = tools.find((item) => item.name === name);
  if (!tool) {
    throw new Error(`未知事件情报工具：${name}`);
  }
  return tool.handler(args || {}, context);
}

async function main() {
  const [command, toolName, jsonArg] = process.argv.slice(2);
  if (!command || command === "list" || command === "list-tools") {
    console.log(JSON.stringify(getTools(), null, 2));
    return;
  }
  if (command !== "call") {
    throw new Error("用法：node src/tools.cjs list-tools | call <tool_name> '<json_args>'");
  }
  const raw = jsonArg !== undefined ? jsonArg : fs.readFileSync(0, "utf8");
  const parsedArgs = raw && raw.trim() ? JSON.parse(raw) : {};
  const result = await callTool(toolName, parsedArgs);
  console.log(JSON.stringify(result, null, 2));
}

if (require.main === module) {
  main().catch((error) => {
    console.error(redactSensitiveText(error && error.stack ? error.stack : error));
    process.exit(1);
  });
}

module.exports = {
  tools,
  getTools,
  callTool,
  redactSensitiveText,
  default: {
    tools,
    getTools,
    callTool,
    redactSensitiveText
  }
};

"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { spawn } = require("node:child_process");

const PLUGIN_DIR = __dirname;
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
          reject(new Error(`Tool runtime returned non-JSON output: ${redactSensitiveText(out).slice(0, 800)}`));
          return;
        }
      }

      if (code !== 0) {
        if (parsed) {
          resolve(parsed);
          return;
        }
        reject(new Error(redactSensitiveText(stderr || stdout || `Tool runtime exited with code ${code}`)));
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
        description: "Semantic topics or keywords. Use an empty array or empty string for no-keyword full-list recent search."
      },
      minutes: { type: "integer", minimum: 1, maximum: 10080, default: 60 },
      pageSize: { type: "integer", minimum: 1, maximum: 30, default: 10 },
      eventSource: { type: "string", description: "Optional filter for no-keyword full-list search." },
      eventType: { type: "string", description: "Optional filter for no-keyword full-list search." },
      isHighValue: {
        oneOf: [{ type: "boolean" }, { type: "string" }],
        description: "Optional filter for no-keyword full-list search."
      },
      noDelivery: {
        type: "boolean",
        default: true,
        description: "Record history for detail lookup but do not mark events as delivered."
      },
      dryRun: { type: "boolean", default: false }
    },
    additionalProperties: false
  },
  listEvents: {
    type: "object",
    properties: {
      start: { type: "string", description: "BJT start time, format YYYY-MM-DD HH:MM:SS. Provide with end." },
      end: { type: "string", description: "BJT end time, format YYYY-MM-DD HH:MM:SS. Provide with start." },
      minutes: { type: "integer", minimum: 1, maximum: 10080, default: 60 },
      eventSource: { type: "string" },
      eventType: { type: "string" },
      isHighValue: { oneOf: [{ type: "boolean" }, { type: "string" }] },
      pageSize: { type: "integer", minimum: 1, maximum: 100, default: 100 },
      limit: { type: "integer", minimum: 0, default: 0 },
      noDelivery: { type: "boolean", default: true }
    },
    additionalProperties: false
  },
  getDetail: {
    type: "object",
    properties: {
      query: {
        type: "string",
        description: "User reference such as '第2条详细看看'. Preferred after a recent search/list tool call."
      },
      eventId: { type: "string", description: "Direct event id lookup. Use with keyword for semantic detail; omit keyword for a no-keyword full-list detail lookup." },
      keyword: { type: "string", description: "Semantic keyword used with eventId." },
      start: { type: "string", description: "Optional full-list detail start time, BJT." },
      end: { type: "string", description: "Optional full-list detail end time, BJT." },
      eventSource: { type: "string" },
      eventType: { type: "string" },
      isHighValue: { oneOf: [{ type: "boolean" }, { type: "string" }] }
    },
    additionalProperties: false
  },
  dailySummary: {
    type: "object",
    properties: {
      respectActiveSetting: {
        type: "boolean",
        default: false,
        description: "When true, skip if the saved push setting is inactive. Manual requests normally leave this false."
      },
      dryRun: { type: "boolean", default: false }
    },
    additionalProperties: false
  },
  runOnce: {
    type: "object",
    properties: {
      runWhenInactive: {
        type: "boolean",
        default: false,
        description: "When true, run one push cycle even if the saved push setting is inactive."
      },
      dryRun: { type: "boolean", default: false }
    },
    additionalProperties: false
  },
  configurePush: {
    type: "object",
    properties: {
      active: { type: "boolean" },
      keywords: {
        oneOf: [
          { type: "array", items: { type: "string" }, maxItems: 3 },
          { type: "string" }
        ]
      },
      eventSource: { type: "string" },
      eventType: { type: "string" },
      isHighValue: { oneOf: [{ type: "boolean" }, { type: "string" }] },
      schedule: {
        type: "string",
        enum: ["5m", "15m", "60m", "24h", "daily-0915", "daily-1245", "daily-1445"]
      },
      pageSize: { type: "integer", minimum: 1, maximum: 30 },
      dailySummaryTime: { type: "string", pattern: "^\\d{1,2}:\\d{2}$" },
      filterObservability: { type: "string", enum: ["off", "metrics", "debug"] }
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
      hours: { type: "integer", minimum: 1, maximum: 720, default: 24 },
      days: { type: "integer", minimum: 0, maximum: 30, default: 0 },
      limitReasons: { type: "integer", minimum: 1, maximum: 100, default: 20 }
    },
    additionalProperties: false
  }
};

const tools = [
  {
    name: "event_intelligence_search_recent",
    description: "Fetch recent platform events by semantic keywords, or full-list recent events when keywords are empty.",
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
    description: "Fetch platform events by time window, source, event type, and high-value filter.",
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
      addValue(args, "--page-size", clampInteger(input.pageSize, 1, 100, 100));
      addValue(args, "--limit", clampInteger(input.limit, 0, 10000, 0));
      addSwitch(args, "--no-delivery", input.noDelivery !== false);
      return runPushRuntime(args);
    }
  },
  {
    name: "event_intelligence_get_detail",
    description: "Get full analysis for an event from recent query history or by direct event id.",
    parameters: schemas.getDetail,
    handler: async (input = {}) => {
      if (hasText(input.query)) {
        return runPushRuntime(["detail-from-ref", "--query", String(input.query)]);
      }
      if (!hasText(input.eventId)) {
        return {
          status: "error",
          detail: "Either query or eventId is required."
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
    description: "Generate the 24-hour platform event level summary using configured topics or configured no-keyword filters.",
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
    description: "Run one configured scheduled push cycle and dedupe against recent delivery history.",
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
    description: "Update event intelligence push configuration without exposing credentials.",
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
    description: "Return redacted runtime status, configured filters, schedule, and recent history summary.",
    parameters: schemas.status,
    handler: async () => runPushRuntime(["status"])
  },
  {
    name: "event_intelligence_filter_metrics",
    description: "Maintenance tool: inspect aggregate rule-filter observability metrics when enabled.",
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

function toOpenClawTool(tool) {
  const definition = publicTool(tool);
  async function execute(toolCallId, params, signal, onUpdate) {
    return tool.handler(params || {}, { toolCallId, signal, onUpdate });
  }
  return {
    ...definition,
    execute,
    handler: (params, context) => tool.handler(params || {}, context || {})
  };
}

function getTools() {
  return tools.map(publicTool);
}

async function callTool(name, args = {}, context = {}) {
  const tool = tools.find((item) => item.name === name);
  if (!tool) {
    throw new Error(`Unknown event intelligence tool: ${name}`);
  }
  return tool.handler(args || {}, context);
}

async function register(openclaw) {
  for (const tool of tools) {
    const registration = toOpenClawTool(tool);
    if (openclaw && typeof openclaw.registerTool === "function") {
      openclaw.registerTool(registration);
    } else if (openclaw && openclaw.tools && typeof openclaw.tools.register === "function") {
      openclaw.tools.register(registration);
    } else if (openclaw && typeof openclaw.addTool === "function") {
      openclaw.addTool(registration);
    }
  }
  return getTools();
}

async function main() {
  const [command, toolName, jsonArg] = process.argv.slice(2);
  if (!command || command === "list" || command === "list-tools") {
    console.log(JSON.stringify(getTools(), null, 2));
    return;
  }
  if (command !== "call") {
    throw new Error("Usage: node index.js list-tools | call <tool_name> '<json_args>'");
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
  toOpenClawTool,
  register,
  activate: register,
  default: {
    tools,
    getTools,
    callTool,
    toOpenClawTool,
    register
  }
};

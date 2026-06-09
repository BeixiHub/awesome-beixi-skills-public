#!/usr/bin/env node

import { createRequire } from "node:module";
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";

const require = createRequire(import.meta.url);
const { getTools, callTool, redactSensitiveText } = require("./src/tools.cjs");

const server = new Server(
  {
    name: "event-intelligence-mcp",
    version: "0.1.0",
  },
  {
    capabilities: {
      tools: {},
    },
  },
);

server.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: getTools().map((tool) => ({
    name: tool.name,
    description: tool.description,
    inputSchema: tool.parameters,
  })),
}));

server.setRequestHandler(CallToolRequestSchema, async (request) => {
  const name = request.params.name;
  const args = request.params.arguments || {};

  try {
    const result = await callTool(name, args);
    const isError = result && typeof result === "object" && result.status === "error";
    return {
      isError,
      content: [
        {
          type: "text",
          text: JSON.stringify(result, null, 2),
        },
      ],
    };
  } catch (error) {
    return {
      isError: true,
      content: [
        {
          type: "text",
          text: JSON.stringify(
            {
              status: "error",
              detail: redactSensitiveText(error && error.stack ? error.stack : error),
            },
            null,
            2,
          ),
        },
      ],
    };
  }
});

const transport = new StdioServerTransport();
await server.connect(transport);

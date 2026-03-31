---
name: QVeris
description: Find and execute third-party APIs via QVeris AI's tool marketplace. Covers finance, economics, healthcare, sports, scientific research, and more. Includes direct REST API integration, caching, error handling, and multi-step tool discovery.
---

## When to Use

Use this skill when you need to:
- Find an API for a specific domain (finance, weather, data, etc.)
- Execute a remote tool with parameters
- Generate production-ready code that calls the tool
- Combine multiple tools in a workflow

### Auto-detect Finance/Investment Tasks

Always assume the user may be referring to a金融对象 even if未 explicitly写“股票/基金”。触发条件包含：
- 输入包含 **公司/代码/品牌/机构名称**（如“四方达”、“贵州茅台”、“宁德时代”、“恒生指数”、“中证500”、“沪深300ETF”）
- 提到 **财务/经营术语**（营收、净利、ROE、现金流、估值、回撤、净值、申购、赎回、分红、净资产、PB/PE、债券收益率、存款、理财、宏观指标等）
- 提到 **投资行为/场景**（买/卖/持有/配置/再平衡/仓位/风控/对冲/投研/调研/策略/套利/择时等）
- 提到 **行业/板块/宏观关键词**（新能源、半导体、地产、银行、消费、PPI、CPI、M2、LPR、GDP等）

只要触发上述任一条件，就将任务视为“金融/投资问题”，并强制执行“Web搜索 + QVeris 搜索”双轨流程，不需要用户额外说明“股票/基金”等词。

## Quick Start

**Direct command** (if you already know the tool ID):
```
用 qveris 执行工具 [tool_id] 参数 [JSON]
```

**Search-first approach** (recommended for unknown tools):
```
用 qveris 搜索 [自然语言查询]
```

**Full workflow** (search → execute → generate code):
```
用 qveris 完整流程：[任务描述]
```

## Core Workflow

### 1. Search Tools

Search for tools using natural language queries.

**Endpoint:** `POST https://qveris.ai/api/v1/search`

**Example request:**
```bash
curl -X POST "https://qveris.ai/api/v1/search" \
  -H "Authorization: Bearer sk-NKX4VnFSFqTStCtGpYIwYxSRfSZSnWbrQz6pT3ZnGww" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "stock price historical data API",
    "limit": 10,
    "session_id": "user_123"
  }'
```

**Key fields in response:**
- `search_id`: Required for subsequent execute calls
- `results[].tool_id`: Unique identifier for the tool
- `results[].params`: Parameter definitions with types, required flags, enums

### 2. Execute Tool

Execute a specific tool with parameters.

**Endpoint:** `POST https://qveris.ai/api/v1/tools/execute?tool_id={tool_id}`

**Example request:**
```bash
curl -X POST "https://qveris.ai/api/v1/tools/execute?tool_id=stock_history_v1" \
  -H "Authorization: Bearer sk-NKX4VnFSFqTStCtGpYIwYxSRfSZSnWbrQz6pT3ZnGww" \
  -H "Content-Type: application/json" \
  -d '{
    "search_id": "search_abc123",
    "session_id": "user_123",
    "parameters": {
      "symbol": "AAPL",
      "start_date": "2025-01-01",
      "end_date": "2025-12-31"
    },
    "max_response_size": 102400
  }'
```

### 3. Generate Production Code

After successful execution, generate ready-to-use code snippets.

**Python example:**
```python
import requests
import json

def call_qveris_tool(tool_id, parameters, search_id=None, session_id=None):
    """Call QVeris AI tool with parameters."""
    url = f"https://qveris.ai/api/v1/tools/execute?tool_id={tool_id}"
    headers = {
        "Authorization": "Bearer sk-NKX4VnFSFqTStCtGpYIwYxSRfSZSnWbrQz6pT3ZnGww",
        "Content-Type": "application/json"
    }
    payload = {
        "search_id": search_id or "default_search",
        "session_id": session_id or "default_session",
        "parameters": parameters,
        "max_response_size": 102400
    }
    
    response = requests.post(url, headers=headers, json=payload, timeout=30)
    response.raise_for_status()
    return response.json()

# Example usage
result = call_qveris_tool(
    tool_id="stock_history_v1",
    parameters={"symbol": "AAPL", "start_date": "2025-01-01", "end_date": "2025-12-31"},
    search_id="search_abc123"
)
print(json.dumps(result, indent=2))
```

## Enhanced Features

### A. Smart Search with Caching

When searching, consider:
- **Query formulation**: Describe functionality, not parameters (e.g., "currency exchange rate API" not "USD to EUR")
- **Result filtering**: Prioritize tools with low `avg_latency_ms` and clear parameter definitions
- **Session management**: Reuse `session_id` for related searches to maintain context

### B. Error Handling & Retries

**Robust execution wrapper:**
```python
import asyncio
from typing import Optional, Dict, Any
import aiohttp
import backoff

class QVerisClient:
    def __init__(self, api_key: str, base_url: str = "https://qveris.ai/api/v1"):
        self.api_key = api_key
        self.base_url = base_url
        self.session: Optional[aiohttp.ClientSession] = None
        
    async def __aenter__(self):
        self.session = aiohttp.ClientSession(
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=aiohttp.ClientTimeout(total=30)
        )
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()
    
    @backoff.on_exception(backoff.expo, aiohttp.ClientError, max_tries=3)
    async def search_tools(self, query: str, limit: int = 10, session_id: str = "") -> Dict[str, Any]:
        """Search for tools with exponential backoff on failure."""
        url = f"{self.base_url}/search"
        payload = {"query": query, "limit": limit}
        if session_id:
            payload["session_id"] = session_id
            
        async with self.session.post(url, json=payload) as resp:
            resp.raise_for_status()
            return await resp.json()
    
    @backoff.on_exception(backoff.expo, aiohttp.ClientError, max_tries=3)
    async def execute_tool(self, tool_id: str, parameters: Dict[str, Any], 
                          search_id: str, session_id: str = "") -> Dict[str, Any]:
        """Execute tool with retry logic."""
        url = f"{self.base_url}/tools/execute?tool_id={tool_id}"
        payload = {
            "search_id": search_id,
            "parameters": parameters,
            "max_response_size": 102400
        }
        if session_id:
            payload["session_id"] = session_id
            
        async with self.session.post(url, json=payload) as resp:
            resp.raise_for_status()
            return await resp.json()
```

### C. Multi-Tool Workflows

**Example: Financial analysis pipeline**
1. Search for "stock historical data" → get `tool_id: stock_history_v1`
2. Search for "financial ratios calculation" → get `tool_id: financial_ratios_v2`
3. Execute both tools and combine results
4. Generate analysis report

### D. Configuration Management

**Environment-based setup:**
```python
import os
from dataclasses import dataclass

@dataclass
class QVerisConfig:
    api_key: str = os.getenv("QVERIS_API_TOKEN", "sk-NKX4VnFSFqTStCtGpYIwYxSRfSZSnWbrQz6pT3ZnGww")
    base_url: str = os.getenv("QVERIS_BASE_URL", "https://qveris.ai/api/v1")
    timeout: int = int(os.getenv("QVERIS_TIMEOUT", "30"))
    max_retries: int = int(os.getenv("QVERIS_MAX_RETRIES", "3"))
    
    @classmethod
    def from_env(cls):
        return cls()
```

## Usage Patterns

### Pattern 1: Direct Tool Execution (已知工具ID)
```
用户：用 qveris 获取苹果公司最近30天的股价
助理：我需要搜索股票数据工具...
[搜索] query: "stock price historical data API"
[执行] tool_id: "stock_history_v1", parameters: {"symbol": "AAPL", "days": 30}
[生成] Python/Node.js 调用代码
```

### Pattern 2: Tool Discovery + Execution (未知工具)
```
用户：帮我分析特斯拉的财务健康状况
助理：我将搜索相关工具...
1. 搜索 "financial statement analysis API"
2. 搜索 "company valuation metrics API"  
3. 执行找到的工具并整合结果
4. 生成分析报告和可复用代码
```

### Pattern 3: Code Generation Only (仅生成代码)
```
用户：生成调用汇率API的Python代码
助理：搜索 "currency exchange rate API" → 找到工具 → 生成包含错误处理和缓存的完整模块
```

## Best Practices

1. **Query formulation**:
   - Bad: "get data for AAPL" (too vague)
   - Good: "stock historical price data API with OHLC format"
   
2. **Parameter validation**:
   - Always check `params` array for required fields and enum values
   - Use examples from `results[].examples.sample_parameters` when available
   
3. **Error handling**:
   - Check `success` field in response
   - Log `error_message` when `success` is false
   - Implement retry with exponential backoff for transient failures
   
4. **Performance**:
   - Cache search results (by query) for 5 minutes
   - Reuse `session_id` across related requests
   - Set appropriate `max_response_size` to avoid large token costs

## Integration with Other Skills

This skill can be combined with:
- **find-skills**: Discover other relevant skills for the task
- **github**: Push generated code to repositories
- **summarize**: Process and summarize tool execution results
- **financial_toolkit**: Integrate with existing financial analysis tools

## Examples

### Example 1: Weather Data
```bash
# Search
curl -X POST "https://qveris.ai/api/v1/search" \
  -H "Authorization: Bearer $QVERIS_TOKEN" \
  -d '{"query": "current weather API with temperature humidity", "limit": 5}'

# Execute  
curl -X POST "https://qveris.ai/api/v1/tools/execute?tool_id=openweathermap_current" \
  -H "Authorization: Bearer $QVERIS_TOKEN" \
  -d '{"search_id": "search_123", "parameters": {"city": "Beijing", "units": "metric"}}'
```

### Example 2: Financial Data
```python
# Generated code
async def get_stock_data(symbol: str, period: str = "1mo"):
    config = QVerisConfig.from_env()
    async with QVerisClient(config.api_key) as client:
        # Search for appropriate tool
        search_result = await client.search_tools(
            f"stock historical data {symbol} {period}"
        )
        
        # Execute first matching tool
        tool = search_result["results"][0]
        result = await client.execute_tool(
            tool_id=tool["tool_id"],
            search_id=search_result["search_id"],
            parameters={"symbol": symbol, "period": period}
        )
        
        return result["result"]["data"]
```

## Troubleshooting

**Common issues:**
1. **Authentication failed**: Check API token format (must start with "sk-")
2. **Tool not found**: Verify `tool_id` matches exactly from search results
3. **Invalid parameters**: Check `params` array for required fields and data types
4. **Rate limiting**: Implement exponential backoff and respect QPS limits

**Debug mode:**
Set environment variable `QVERIS_DEBUG=1` to log full request/response details.

---

*Note: This skill uses the QVeris AI API. API token from SKILL.md is for demonstration; replace with your own token for production use.*

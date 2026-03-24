---
name: finance-dual-track
description: Automatically handle finance, investment, stock, fund, sector, macro, valuation, earnings, and business-analysis questions with a dual-track workflow: web research plus QVeris structured data, then produce a professional readable briefing for commercially literate readers.
---

# Finance Dual Track

Use this skill for any finance, investment, stock, fund, sector, macro, valuation, earnings, or business-analysis question where both market narrative and hard data matter.

This skill is not just for ticker lookups. It also applies when the user asks for:
- 公司分析 / business analysis
- 股票、基金、ETF、指数判断
- 财报、估值、盈利质量、现金流、ROE、分红、回撤
- 行业、板块、景气度、政策影响
- 宏观变量（CPI, PPI, M2, LPR, GDP, 利率, 汇率）
- 对比研究、投研纪要、管理层口径、催化与风险

## Trigger Heuristics

Trigger this skill whenever the request contains one or more of the following:

1. Target-like objects
- Company names, brands, stock codes, fund names, ETF names, index names
- Chinese examples: 贵州茅台、宁德时代、腾讯、沪深300、中证500、创业板、恒生科技、标普500
- English examples: Apple, NVIDIA, Alphabet, Tesla, TSMC, S&P 500, Nasdaq 100

2. Finance language
- 营收、净利、毛利率、经营利润、净利率、ROE、ROIC、现金流、自由现金流、CapEx、分红、回购、估值、PE、PB、EV/EBITDA、指引、业绩、财报、资金流

3. Investor/business intent
- 是否值得买、怎么看、分析、研究、对比、拆解、复盘、风险、催化、跟踪指标、商业模式、竞争格局、行业趋势

If uncertain, err on the side of triggering this skill.

## Core Contract

For triggered requests, do both lanes whenever possible:

### Lane A: Web research
Gather qualitative context:
- latest news and developments
- company guidance and management commentary
- industry context and policy changes
- catalyst flow and market expectations
- important primary sources when available

Prefer strong sources in this order:
1. company IR / filings / exchange disclosures
2. regulator / official data
3. reputable financial media
4. high-signal secondary summaries

### Lane B: QVeris structured data
Try to pull the most relevant structured data:
- company basics
- real-time / recent quotation
- income statement
- balance sheet
- cash flow statement
- money flow
- macro series
- other iFinD or marketplace tools as appropriate

Token resolution order:
1. env `QVERIS_API_TOKEN`
2. `workspace/secrets/qveris_token.txt`
3. if unavailable, continue with web-only and state that the structured lane was unavailable

QVeris workflow:
1. search tools first if tool id is not already known
2. execute the best-fit tool
3. if latest period is missing, back off to the nearest available quarter or full year
4. do not silently fabricate unavailable figures

## Recommended QVeris Targets

### For company / stock analysis
Default targets to try:
- `ths_ifind.company_basics.v1`
- `ths_ifind.real_time_quotation.v1`
- `ths_ifind.financial_statements.v1`
- `ths_ifind.money_flow.v1` when available

### For macro / rates / economy
Search relevant QVeris macro tools first, then execute the closest official or market-data source.

## Reasoning Rules

When synthesizing results:
- separate **facts**, **market narrative**, and **your inference**
- if web context and structured data do not perfectly match, explain the mismatch
- distinguish clearly between latest reported period and trailing-twelve-month / current market pricing
- use absolute dates when discussing earnings, policy, releases, or guidance
- do not hide uncertainty

## Output Style Contract

Default audience:
- financially literate reader
- professional business / investment perspective
- does not want AI theater or tool dumps

Writing rules:
- 结论先行
- 结构化
- 高可读性
- 少废话
- 不暴露工具执行痕迹
- 不堆原始 JSON
- 把数字翻译成 business meaning

### Default output template

Use this structure unless the user asks for another format:

1. **核心结论**
- 3-6 bullets
- directly answer the implied investment/business question

2. **发生了什么 / 现在处于什么阶段**
- latest situation
- what changed recently

3. **业务与基本面判断**
- business model
- core drivers
- competitive position
- margins / cash flow / balance sheet if relevant

4. **关键数据点**
- latest quote / valuation if relevant
- latest available financial statement highlights
- operating or macro indicators relevant to the thesis

5. **催化剂**
- next earnings, policy, product cycle, industry turn, price cycle, etc.

6. **核心风险**
- business risk
- valuation risk
- policy / macro risk
- execution risk

7. **跟踪指标**
- what to watch next

### Output variants

#### Quick view
Use when the user asks a broad short question like “怎么看英伟达”:
- thesis
- 3 drivers
- 3 risks
- watchlist

#### Deep dive
Use when the user asks “给我完整分析/报告”:
- business snapshot
- financial quality
- valuation
- capital allocation
- industry context
- catalysts and risks
- final judgment

#### Compare
Use when the user asks to compare two or more names:
- growth
- margins
- cash flow
- balance sheet
- valuation
- risk / catalyst
- final relative view

## Anti-Patterns

Do not:
- answer finance questions from generic memory only
- rely on one lane when two lanes are available
- paste long raw search output
- present speculation as fact
- over-focus on trivia when the user wants a business conclusion
- default to textbook explanations when company-specific evidence is available

## Lightweight Execution Checklist

For every triggered request:
1. identify the target and likely code
2. run web search for recent context
3. run QVeris for structured data if available
4. reconcile facts and narrative
5. write a concise professional briefing

## Fallback Behavior

If QVeris fails:
- say structured financial data was unavailable in this turn
- still complete the web lane
- if possible, use previously written local files only when they were created in the same task or explicitly provided

If web search is thin but QVeris works:
- provide a data-first briefing and say recent narrative context was limited

## Good Outcome Standard

A strong answer should feel like:
- something a buy-side analyst could skim quickly
- something a PM could forward internally
- something a business reader could use in discussion immediately

The answer should leave the reader with:
- what matters
- why it matters
- what could change the view

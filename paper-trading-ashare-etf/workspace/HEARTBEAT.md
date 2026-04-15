# HEARTBEAT.md - 定时任务

## 每日持仓晨报

**触发时间**：每个交易日早上 9:00（北京时间，`0 9 * * *`）

**cron 表达式**：`0 9 * * *`

**执行内容**：

利用 OpenClaw 的 cron 机制，在工作区加载时自动注册一个每日 9:00 触发的定时任务。任务触发时：

1. 进入 skill 目录 `workspace/skills/paper-trading-ashare-etf/`
2. 依次调用：
   ```bash
   python trading_service.py account
   python trading_service.py holdings
   ```
3. 解析两个命令返回的 JSON，按以下格式主动推送给用户：

   ```
   📅 早安！这是今天的持仓晨报 (YYYY-MM-DD 09:00)
   ━━━━━━━━━━━━━━━━━━━━━━━━

   💰 账户总览
   · 总资产：¥XXX,XXX.XX
   · 可用资金：¥XXX,XXX.XX
   · 今日盈亏：+/-¥X,XXX.XX （+/-X.XX%）
   · 累计收益率：+/-XX.XX%

   📊 当前持仓（共 N 只）
   1. 贵州茅台 (600519) ｜ 100 股 ｜ 成本 1680.00 ｜ 现价 1720.00 ｜ 收益 +2.38% ｜ 仓位 35.2%
   2. 宁德时代 (300750) ｜ 200 股 ｜ 成本 195.00 ｜ 现价 188.50 ｜ 收益 -3.33% ｜ 仓位 22.1%
   ...

   💡 需要调仓或查看某只票的详情，随时告诉我。
   ```

   字段映射：
   - 总资产 ← `account.summary.total_assets`
   - 可用资金 ← `account.summary.available_asset`
   - 今日盈亏 ← `account.summary.today_profit_and_loss` + `today_profit_and_loss_rate`
   - 累计收益率 ← 用 `account.summary.total_profit` / `(total_assets - total_profit)` 计算
   - 持仓明细 ← `holdings.items[]`，逐只列出代码、名称、数量、成本、现价、收益率、仓位

4. 如果接口调用失败（网络错误、鉴权失败等），**不要沉默**，直接推送错误提示：
   ```
   ⚠️ 今日晨报获取失败：<具体错误 message>
   请检查 state/account_config.json 或稍后手动查询。
   ```

5. 如果持仓为空（`holdings.items` 长度为 0），仍然推送账户总览部分，持仓部分写 "当前空仓，可用资金 ¥XXX,XXX.XX"。

## 启停规则

- **启动**：进入本 workspace 时自动注册 cron，无需用户显式触发
- **停止**：当用户明确说"关掉晨报"、"不要每天推送了"、"停止定时任务"时，移除该 cron 任务
- **修改时间**：当用户说"改到早上 8 点"、"改成下午 3 点收盘后推送"等，更新 cron 表达式并重新注册
- **暂停一天**：当用户说"明天别推了"，跳过下一次执行（可通过记录 `skip_next: true` 到 HEARTBEAT 状态文件实现）

## 注意事项

- 时区统一为北京时间（UTC+8），`0 9 * * *` 中的 9 指本地 9 点
- 周末和法定节假日仍会触发——如果接口返回的 `today_profit_and_loss` 为 0 且没有新成交，说明当天不是交易日，仍然照常推送（用户可以看到账户状态）
- 晨报推送时不调用 `deals-today` 或 `delegates-today`，避免早高峰接口压力；用户如果关心成交，可以自己追问
- 推送内容必须是自然中文，**不允许把原始 JSON 贴给用户**

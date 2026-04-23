# 接入说明

## 目录

- `SKILL.md`
  - 主流程与行为规则
- `scripts/`
  - 运行时脚本
- `references/`
  - 补充说明
- `state/`
  - 本地状态和缓存

## 依赖

- Python 3
- `requests`
- `pycryptodome`

安装方式：

```bash
pip install -r requirements.txt
```

## 环境变量

### 默认需要

- `PAPER_TRADING_NGW_BASE_URL`
  - 默认值：`https://apicore.niuguwang.com`
- `PAPER_TRADING_NGW_AES_KEY`
  - 默认值已内置，也可以显式配置
- `PAPER_TRADING_NGW_AES_IV`
  - 默认值已内置，也可以显式配置
- `PAPER_TRADING_REQUEST_TIMEOUT_SECONDS`
  - 默认值：`20`

### 可选

只有在你要通过网站后端注册/查状态时，才需要：

- `PAPER_TRADING_BACKEND_BASE_URL`
- `PAPER_TRADING_BACKEND_SECRET`

默认模式不依赖后端数据库，也不依赖网站注册接口。

## 首次使用

建议先运行：

```bash
python trading_service.py doctor
python trading_service.py status
```

如果还没绑定，直接注册：

```bash
python trading_service.py register --phone "13800138000" --save-state
python trading_service.py register --real-name "张三" --phone "13800138000" --save-state
python trading_service.py status --save-state
```

如果你明确要走网站后端，再使用：

```bash
python trading_service.py register --real-name "张三" --phone "13800138000" --via-backend --save-state
```

## 状态文件

### `state/binding_state.json`

保存本地绑定状态，例如：

- `claw_token`
- `real_name`
- `phone`
- `masked_phone`
- `registration_status`
- `binding_status`
- `account_id`
- `user_name`
- `can_trade`
- `registration_source`

### `state/inner_code_cache.json`

保存股票与 `innerCode` 的本地映射缓存。

## 已知限制

- 牛股网 V2 下单结果不能只看 `success`
- 牛股网 V2 撤单结果当前不稳定
- `innerCode` 不一定能自动解析，必要时要先维护本地映射


# call_remote_phase_api.py 运行原理

> 这是 Portfolio Health Check 客户端的 HTTP 桥接脚本，负责将本地组装好的 JSON payload 发送到远端 API 服务器，并接收结果。

---

## 一、定位与职责

```
┌──────────────┐     HTTP POST      ┌──────────────────┐
│  Claude Skill │ ──────────────────→ │  远端 API 服务器   │
│  (本地客户端)   │  call_remote_...  │  由环境变量指定     │
│              │ ←────────────────── │                  │
└──────────────┘    JSON / PDF      └──────────────────┘
```

本脚本**不做任何业务计算**，只是一个"传话筒"：
- 读取本地 JSON 文件 → 发送到远端 → 接收并输出结果

---

## 二、支持的三种调用模式

| 模式 | 命令 | 远端路径 | 输入 | 输出 |
|------|------|---------|------|------|
| Phase 2 数据 | `python call_remote_phase_api.py phase2 payload.json` | `/api/v1/phase-2/deep-diagnosis` | 持仓 + 4 参数 JSON | 诊断结果 JSON |
| Phase 2 PDF | `python call_remote_phase_api.py phase2_pdf payload.json` | `/api/v1/phase-2/deep-diagnosis/pdf` | 同上 | PDF 二进制文件 |
| Phase 3 | `python call_remote_phase_api.py phase3 payload.json` | `/api/v1/phase-3/optimization` | 诊断结果 + 约束 JSON | 优化处方 JSON |

---

## 三、执行流程

```
1. 解析命令行参数
   ├── phase:        phase2 | phase2_pdf | phase3
   ├── payload_file: 本地 JSON 文件路径
   ├── --base-url:   API 地址（可选，默认读环境变量）
   ├── --token:      Bearer Token（可选，默认读环境变量）
   └── --output:     结果写入路径（可选，默认输出到 stdout）

2. 读取 payload JSON 文件
   └── json.loads(payload_file)

3. 构建 HTTP 请求
   ├── URL = base_url + 路径（由 phase 决定）
   ├── Method = POST
   ├── Headers:
   │   ├── Content-Type: application/json
   │   └── Authorization: Bearer <token>（如有）
   └── Body = payload JSON（UTF-8 编码）

4. 发送请求（urllib.request.urlopen）
   └── 超时: 180 秒

5. 处理响应
   ├── phase2 / phase3:
   │   ├── 读取响应 body → 解析为 JSON
   │   └── 写入 --output 文件 或 输出到 stdout
   └── phase2_pdf:
       ├── 校验 Content-Type 是否为 application/pdf
       ├── 读取响应 body → 二进制 bytes
       └── 写入 --output 文件 或 输出到 stdout（二进制）

6. 错误处理
   ├── HTTPError (4xx/5xx) → 读取错误详情 → SystemExit
   └── URLError (连不上)    → 输出连接错误 → SystemExit
```

---

## 四、配置项

### 环境变量

| 变量名 | 用途 | 默认值 |
|--------|------|--------|
| `PORTFOLIO_API_BASE_URL` | API 服务器地址 | 无，必须显式提供 |
| `PORTFOLIO_API_TOKEN` | Bearer 认证 Token | 空（不认证） |

### 命令行参数

| 参数 | 说明 |
|------|------|
| `phase` | 必填，`phase2` / `phase2_pdf` / `phase3` |
| `payload_file` | 必填，JSON payload 文件路径 |
| `--base-url` | 可选，覆盖环境变量；未提供时必须设置 `PORTFOLIO_API_BASE_URL` |
| `--token` | 可选，覆盖环境变量 |
| `--output` | 可选，结果写入文件路径（不指定则输出到 stdout） |

优先级：`--命令行参数` > `环境变量`

---

## 五、核心函数

### `build_url(base_url, phase) → str`
根据 phase 拼接完整 API URL。

### `call_api(base_url, token, phase, payload) → dict`
发送 JSON 请求，返回解析后的 JSON dict。用于 phase2 和 phase3。

### `download_pdf(base_url, token, payload) → (bytes, content_disposition)`
发送 PDF 请求，校验 Content-Type 后返回 PDF 二进制内容。用于 phase2_pdf。

### `main() → int`
命令行入口，串联参数解析 → 读文件 → 调 API → 写输出。

---

## 六、使用示例

```bash
# Phase 2：深度诊断（结果输出到文件）
python call_remote_phase_api.py phase2 state/phase2_payload.json \
  --output state/phase2_result.json

# Phase 2 PDF：生成 PDF 报告
python call_remote_phase_api.py phase2_pdf state/phase2_payload.json \
  --output report.pdf

# Phase 3：优化处方
python call_remote_phase_api.py phase3 phase3_payload.json \
  --output optimization_result.json

# 使用自定义 API 地址和 Token
PORTFOLIO_API_BASE_URL=http://localhost:9000 \
PORTFOLIO_API_TOKEN=my-secret-token \
python call_remote_phase_api.py phase2 payload.json
```

---

## 七、注意事项

1. **超时**：固定 180 秒，phase2（数据量大）和 phase2_pdf（渲染耗时）都用同一超时
2. **轻量重试**：超时类 `URLError` 会自动重试 1 次；HTTP 错误仍会直接退出
3. **无日志**：脚本没有 logging，排查问题需要看 stderr 输出
4. **PDF 校验**：下载 PDF 时会检查 Content-Type，如果服务端返回非 PDF 内容会报错退出
5. **编码**：payload 使用 `ensure_ascii=False` 编码，支持中文字段名

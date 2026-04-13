# OpenClaw Client Deployment Guide

这份文档回答两个问题：

1. 当 Phase 2 / Phase 3 已经封装在云端私有 API 后，OpenClaw 客户端应该保留哪些文件。
2. 为了保护 Phase 2 / Phase 3 源码，客户端部署包里应该删掉哪些文件。

本文档面向开发者和部署同学。

## 1. 推荐架构

推荐把系统拆成两层：

- OpenClaw 客户端：负责对话、参数收集、结果转述
- 云端私有 API：负责 Phase 2 / Phase 3 计算

OpenClaw 客户端不再本地执行：

- `pipeline_main.py`
- `prescription_main.py`
- `compute/`
- `prescribe/`

客户端只调用远端 API。

## 2. 客户端应该保留的内容

以下内容建议继续部署到 OpenClaw 客户端：

- `SKILL.md`
- `portfolio-quick-diagnosis/`
- `portfolio-deep-diagnosis/`
- `portfolio-optimization/`
- `state/`
- `call_remote_phase_api.py`

说明：

- `analysis_prompt.md` 和 `report_prompt.md` 位于 `portfolio-quick-diagnosis/` 子目录下，会随子技能目录一起保留。
- `portfolio-deep-diagnosis/` 和 `portfolio-optimization/` 这两个 skill 目录需要保留，因为它们还承担参数收集和结果解释。
- 这两个 skill 不再本地跑私有计算，只负责调用远端 API。

## 3. 客户端建议删除的内容

以下内容建议不要部署到 OpenClaw 客户端：

- `scripts/portfolio-health-check/pipeline_main.py`
- `scripts/portfolio-health-check/diagnosis.py`
- `scripts/portfolio-health-check/diagnosis_schema.py`
- `scripts/portfolio-health-check/data_loader.py`
- `scripts/portfolio-health-check/prescription_main.py`
- `scripts/portfolio-health-check/prescription.py`
- `scripts/portfolio-health-check/structured_output.py`
- `scripts/portfolio-health-check/generate_report_html.py`
- `scripts/portfolio-health-check/html_pdf.py`
- `scripts/portfolio-health-check/run_sample.py`
- `scripts/portfolio-health-check/_diagnosis_data.json`
- `scripts/portfolio-health-check/compute/`
- `scripts/portfolio-health-check/prescribe/`
- `scripts/portfolio-health-check/tests/`

这些文件和目录基本都属于 Phase 2 / Phase 3 私有实现细节，客户端没有必要持有。

## 4. 客户端环境变量

OpenClaw 客户端至少需要这两个配置：

- `PORTFOLIO_API_BASE_URL`
- `PORTFOLIO_API_TOKEN`（如服务端启用鉴权）

当前桥接脚本 [call_remote_phase_api.py](./call_remote_phase_api.py) 不再内置默认服务地址。

这意味着：

- OpenClaw 运行环境必须显式设置 `PORTFOLIO_API_BASE_URL`，或在命令行里传 `--base-url`
- 如果后续切换环境，只需要覆盖 `PORTFOLIO_API_BASE_URL`
- `PORTFOLIO_API_TOKEN` 仍然保留为可选鉴权项

当前团队默认部署可直接预置：

```bash
export PORTFOLIO_API_BASE_URL="http://82.157.41.134:9000"
```

这样客户端代码仍然不硬编码服务器地址，但运行环境会默认指向当前固定服务。

完整示例：

```bash
export PORTFOLIO_API_BASE_URL="http://82.157.41.134:9000"
export PORTFOLIO_API_TOKEN="your-token-if-needed"
```

## 5. 客户端如何调用远端 Phase 2 / Phase 3

OpenClaw skill 不应该直接写长串 HTTP 逻辑，而应该调用本地桥接脚本：

```bash
cd ~/.openclaw/workspace/skills/portfolio-health-check && \
python call_remote_phase_api.py phase2 /tmp/portfolio_payload.json --output /tmp/portfolio_output/diagnosis_result.json
```

如果用户明确要求下载 PDF 报告，调用：

```bash
cd ~/.openclaw/workspace/skills/portfolio-health-check && \
python call_remote_phase_api.py phase2_pdf /tmp/portfolio_payload.json --output /tmp/portfolio_output/diagnosis_report.pdf
```

```bash
cd ~/.openclaw/workspace/skills/portfolio-health-check && \
python call_remote_phase_api.py phase3 /tmp/phase3_payload.json --output /tmp/optimization_output/optimization_result.json
```

## 6. 技能修改原则

需要修改三个技能文件：

- `SKILL.md`
- `portfolio-deep-diagnosis/SKILL.md`
- `portfolio-optimization/SKILL.md`

修改目标只有一个：

- 保留原有收集信息和解释结果的职责
- 删除本地 Phase 2 / 3 计算入口
- 改为调用远端私有 API

## 7. 不删也能跑，但不安全

如果这些私有脚本继续留在客户端，系统通常仍然能跑。

但这样会带来两个问题：

- 客户端拿到 Phase 2 / Phase 3 源码
- 后续 OpenClaw skill 可能继续误用本地脚本，绕过你的私有 API

所以如果目标是保护私有实现，建议同时做两件事：

1. 删除本地私有脚本
2. 修改 skill，明确只允许调用远端 API

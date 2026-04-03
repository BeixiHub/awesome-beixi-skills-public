#!/bin/bash
# arkClawDemo 本地启动脚本
set -e
cd "$(dirname "$0")"

# ── 检查前置条件 ──────────────────────────────────────────────
if [ ! -f openclaw.env ]; then
  echo "❌ openclaw.env 不存在。请复制模板并填入 API Key："
  echo "   cp openclaw.env.example openclaw.env"
  exit 1
fi

# ── 构建 runtime 目录 ─────────────────────────────────────────
echo "🔧 构建 runtime 目录..."
RUNTIME="runtime"
mkdir -p "$RUNTIME"

# 同步 workspace（skill 文件可能有更新）
rsync -a --delete workspace/ "$RUNTIME/workspace/"

# 复制 Docker 配置
cp openclaw-docker.json "$RUNTIME/openclaw.json"

# 复制 identity（如果存在）
if [ -d identity ]; then
  rsync -a identity/ "$RUNTIME/identity/"
fi

echo "✅ runtime 目录就绪"

# ── 启动容器 ────────��──────────────────────────────────────────
echo "🚀 启动 arkClawDemo..."
docker compose down 2>/dev/null || true
docker compose up -d --build

# ── 等待 gateway 就绪 ──────────────────────────────────────────
echo "⏳ 等待 gateway 启动..."
READY=false
for i in $(seq 1 30); do
  if docker exec arkclaw-demo node -e "
    const http=require('http');
    http.get('http://127.0.0.1:18789', r => {
      process.exit(r.statusCode >= 200 ? 0 : 1);
    }).on('error', () => process.exit(1));
  " 2>/dev/null; then
    READY=true
    break
  fi
  sleep 1
done

if [ "$READY" != "true" ]; then
  echo ""
  echo "❌ Gateway 未在 30 秒内就绪"
  echo "   查看日志: docker logs arkclaw-demo --tail 50"
  exit 1
fi

# ── 输出连接信息 ─────────��─────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━���━━━━━━━━━━━━━━━━━━━"
echo "✅ arkClawDemo 已启动"
echo ""
echo "   Web UI:  http://localhost:18810"
echo "   密码:    见 openclaw.env 中的 OPENCLAW_GATEWAY_PASSWORD"
echo ""
echo "   查看日志:  docker logs arkclaw-demo -f"
echo "   停止:      docker compose down"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━��━━━━━━━━━━━��━━━━━━━━━━━━"

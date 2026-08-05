#!/usr/bin/env bash
# 情报 Planner Agent — 首次初始化脚本（独立实例）
#
# 作用: 在容器内初始化 intel-planner profile
#   - 配置 DeepSeek 模型（key 从环境变量注入）
#   - 创建精简 profile
#   - 禁用无关工具（省 token）
#   - 安装情报方法论 skill
#
# 前置: 已设置 DEEPSEEK_API_KEY 并 docker compose up -d
# 用法: ./setup.sh

set -e
cd "$(dirname "$0")"

HERMES="/opt/hermes/.venv/bin/hermes"
PROFILE="intel-planner"

# 读取专用 key（deploy/intel-planner/.env，由用户创建；绝不读宿主机 ~/.hermes/.env）
ENV_FILE="$(dirname "$0")/.env"
if [ ! -f "$ENV_FILE" ]; then
  echo "❌ 未找到 $ENV_FILE"
  echo "   请先: cp .env.example .env 并填入为 Docker 专用新建的 DeepSeek API key"
  echo "   （不要使用宿主机 ~/.hermes/.env 里的 key）"
  exit 1
fi
KEY=$(grep -E '^DEEPSEEK_API_KEY=' "$ENV_FILE" | head -1 | cut -d= -f2)
API_KEY=$(grep -E '^HERMES_INTEL_API_KEY=' "$ENV_FILE" | head -1 | cut -d= -f2)

if [ -z "$KEY" ] || [ "$KEY" = "sk-你的新key" ]; then
  echo "❌ $ENV_FILE 中的 DEEPSEEK_API_KEY 未设置或还是模板值"
  echo "   请填入为 Docker 专用新建的 DeepSeek API key"
  exit 1
fi
if [ -z "$API_KEY" ] || [ "$API_KEY" = "openssl-rand-hex-32" ]; then
  echo "❌ $ENV_FILE 中的 HERMES_INTEL_API_KEY 未设置或还是模板值"
  echo "   生成一个: openssl rand -hex 32"
  exit 1
fi

echo "=== 1. 确认容器内 hermes ==="
docker exec hermes-intel-planner "$HERMES" --version

echo "=== 2. 写入 .env（模型 key，专用 key）==="
docker exec hermes-intel-planner sh -c "echo 'DEEPSEEK_API_KEY=$KEY' > /opt/data/.env"
docker exec hermes-intel-planner sh -c "echo 'API_SERVER_KEY=$API_KEY' >> /opt/data/.env"

echo "=== 3. 创建 profile: $PROFILE ==="
docker exec hermes-intel-planner "$HERMES" profile create "$PROFILE" 2>/dev/null \
  || echo "profile 已存在或创建失败，继续"

echo "=== 4. 配置模型 (DeepSeek) ==="
docker exec hermes-intel-planner "$HERMES" --profile "$PROFILE" config set model.default deepseek-v4-pro
docker exec hermes-intel-planner "$HERMES" --profile "$PROFILE" config set model.provider deepseek

echo "=== 5. 禁用无关工具（只保留情报所需核心）==="
# 保留: web(搜索+提取), terminal(写代码), file(读写), skills, delegation
# 禁用: browser, vision, image_gen, video, tts, stt, messaging, cronjob, kanban, homeassistant, todo
for toolset in browser vision image_gen video tts stt messaging cronjob kanban homeassistant todo memory session_search; do
  docker exec hermes-intel-planner "$HERMES" --profile "$PROFILE" tools disable "$toolset" 2>/dev/null \
    && echo "  已禁用: $toolset" || echo "  (跳过 $toolset)"
done

echo "=== 6. 安装情报方法论 skill ==="
docker exec hermes-intel-planner sh -c "mkdir -p /opt/data/profiles/$PROFILE/skills/think-tank-intel"
docker exec hermes-intel-planner sh -c "cp /opt/methodology/INVESTIGATION_METHODOLOGY.md /opt/data/profiles/$PROFILE/skills/think-tank-intel/SKILL.md"
echo "  ✅ 已安装情报方法论 skill"

echo "=== 7. 配置 API Server（独立端口 8643 + key）==="
docker exec hermes-intel-planner "$HERMES" --profile "$PROFILE" config set platforms.api_server.enabled true
docker exec hermes-intel-planner "$HERMES" --profile "$PROFILE" config set platforms.api_server.host 0.0.0.0
docker exec hermes-intel-planner "$HERMES" --profile "$PROFILE" config set platforms.api_server.port 8643
docker exec hermes-intel-planner "$HERMES" --profile "$PROFILE" config set platforms.api_server.key "$API_KEY"
docker exec hermes-intel-planner "$HERMES" --profile "$PROFILE" config set platforms.api_server.cors_origins "*"

echo ""
echo "=== 完成 ==="
echo "启动该 profile 的 gateway:"
echo "  docker exec hermes-intel-planner $HERMES -p $PROFILE gateway start"
echo "验证 API:"
echo "  curl http://localhost:8643/v1/models"

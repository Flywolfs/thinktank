#!/usr/bin/env bash
# 验证情报 Planner Agent 是否就绪
set -e

echo "=== 1. 容器状态 ==="
docker ps --filter name=hermes-intel-planner --format "{{.Names}} {{.Status}}"

echo ""
echo "=== 2. API Server 健康检查 ==="
curl -s --max-time 10 http://localhost:8642/v1/models | head -c 300 || echo "⚠️ API 未就绪（可能还在启动）"

echo ""
echo "=== 3. 测试 plan 决策（发给 intel-planner）==="
KEY="${HERMES_INTEL_API_KEY:-intel-planner-local-key}"
curl -s --max-time 120 http://localhost:8642/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $KEY" \
  -d '{
    "model": "hermes-agent",
    "messages": [{"role": "user", "content": "你是情报调查计划制定专家。请为实体「雷军」（hints: 小米汽车）制定调查维度模板，输出 JSON 格式的维度列表，每个维度含 name/methodology_source/rationale/queries/priority。"}]
  }' | head -c 800

echo ""
echo "=== 完成 ==="

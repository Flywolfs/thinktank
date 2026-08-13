# 情报 Planner Agent — 部署指南

## 架构

独立 Docker 实例，专职做两件事：
1. **情报 plan 决策** — 接收实体+hints → 返回维度模板 JSON（对比路线 A）
2. **额外爬虫代码编写** — 为 Phase C 动态工具创建做准备

```
┌────────────────────────────────────────────┐
│ Docker: hermes-intel-planner               │
│  镜像: nousresearch/hermes-agent           │
│  数据: ~/.hermes-intel-planner (独立)      │
│  profile: intel-planner                    │
│  API: http://localhost:8643                │
│  工具: web + terminal + file + skills      │
│  禁用: browser/vision/tts/messaging/...    │
└────────────────────────────────────────────┘
        ▲ 调用 /v1/chat/completions
        │ (think_tank 系统将来通过这里获取 plan)
```

## 步骤

### 0. 配置专用 DeepSeek API key（一次）

```bash
cd ~/Documents/think_tank/deploy/intel-planner

# 为 Docker 容器新建一个 DeepSeek API key（不要用宿主机 ~/.hermes/.env 里的 key）
# https://platform.deepseek.com/api_keys
cp .env.example .env
# 编辑 .env，填入:
#   DEEPSEEK_API_KEY=sk-为这个docker新建的key
#   HERMES_INTEL_API_KEY=<openssl rand -hex 32>
```

### 1. 启动容器

```bash
cd ~/Documents/think_tank/deploy/intel-planner
docker compose up -d
```

> docker compose 自动读取同目录 `.env` 里的 `DEEPSEEK_API_KEY` 注入容器，无需手动传。

### 2. 首次初始化

```bash
./setup.sh
```

setup.sh 会：
- 从 `deploy/intel-planner/.env` 读取专用 key（绝不读宿主机 ~/.hermes/.env）
- 写入容器 .env（模型 key + API key）
- 创建 intel-planner profile
- 配置 DeepSeek 模型
- 禁用无关工具（省 token）
- 安装情报方法论 skill
- 配置 API Server (8643)

### 3. 启动该 profile 的 gateway

```bash
docker exec hermes-intel-planner /opt/hermes/.venv/bin/hermes -p intel-planner gateway start
```

### 4. 验证

```bash
curl http://localhost:8643/v1/models
# 应返回 OpenAI 兼容的模型列表

# 测试 plan 决策（API key 用 .env 里的 HERMES_INTEL_API_KEY）
curl -s http://localhost:8643/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <HERMES_INTEL_API_KEY>" \
  -d '{
    "model": "hermes-agent",
    "messages": [{"role": "user", "content": "你是情报调查计划专家。为实体「蒋方舟」（hints: 政治立场、相关人物）制定调查维度模板，输出 JSON。"}]
  }'
```

## 常见问题

- **容器起不来 / API 不通**：`docker logs hermes-intel-planner` 看日志
- **动态工具生成超时**：Hermes 写代码是"写文件 + 框架轮询"模式，
  单次 HTTP 只负责启动任务（60s 超时），真正等待由轮询承担（默认 300s，可调 HERMES_POLL_TIMEOUT）。
  如果长时间未生成，检查 `docker exec hermes-intel-planner ls /opt/tools_dynamic/` 看文件是否写入
- **想改工具集**：`docker exec hermes-intel-planner hermes -p intel-planner tools`（交互式）
- **重启**：`docker compose restart`
- **删除**：`docker compose down`（数据保留在 ~/.hermes-intel-planner）

## 文件

```
deploy/intel-planner/
├── docker-compose.yml   # 容器编排
├── setup.sh             # 首次初始化
└── verify.sh            # 就绪验证
```

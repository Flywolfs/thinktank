# 🕵️ 智库情报系统 (think_tank)

实体情报深挖系统 — 输入人名/公司/事件，自动从互联网多源深挖实体信息，
形成证据链完整的情报分析报告，并沉淀为可积累的 Neo4j 知识图谱资产。

核心能力：
- **Plan 决策层**：自研多轮推理 + Docker Hermes Agent 双路线（auto 融合，可配置）
- **Execute 执行层**：7 个数据源 + 4 个处理步骤，全部工具化、可审计、可回放
- **动态工具创建**：缺数据源时由 Hermes 写爬虫 → LLM 安全审查 → 自动注册
- **图谱递归深挖**：实体归一化防环、证据累积、调查血统回溯
- **UI 数据源状态检测**：快速/深度检测每个爬虫是否正常

---

## 一、架构总览

```
┌────────────────────────────────────────────────────────────┐
│                        Vue 3 前端 (5173)                    │
│   发起调查 / 计划可视化 / 报告审阅 / 图谱交互 / 数据源状态   │
└──────────────────────────┬─────────────────────────────────┘
                           │ HTTP
┌──────────────────────────▼─────────────────────────────────┐
│                   FastAPI 后端 (8899)                       │
│  /api/investigate  /api/jobs  /api/graph  /api/tools       │
│  /api/health/providers (数据源状态)  /api/tools/dynamic     │
└───────┬──────────────┬──────────────┬──────────────┬────────┘
        │              │              │              │
┌───────▼───────┐ ┌────▼─────┐ ┌──────▼──────┐ ┌─────▼──────┐
│ LangGraph 图   │ │ Tool      │ │ 动态工具     │ │ 日志/审计   │
│ plan→search→  │ │ Registry  │ │ Hermes 生成 │ │ JSONL 回放  │
│ …→review→graph │ │ 16 工具   │ │ LLM 审查    │ │            │
└───────┬───────┘ └────┬─────┘ └──────┬──────┘ └────────────┘
        │              │              │
┌───────▼──────────────▼──────────────▼──────────────────────┐
│                      数据源 (7 Provider)                    │
│  Wikipedia REST · Serper(Google) · SerpApi(Baidu) ·         │
│  bilibili-cli(B站) · MediaCrawler(知乎/小红书/微博·CDP)     │
│  Qwen3-ASR(视频转文字)                                      │
└─────────────────────────────────────────────────────────────┘
        │
┌───────▼────────────────────────────────────────────────────┐
│               Neo4j 知识图谱 (7687)                         │
│  实体归一化 / 关系证据累积 / 血统边 / 别名增量学习           │
└────────────────────────────────────────────────────────────┘
```

## 二、前置依赖

| 依赖 | 用途 | 安装 |
|------|------|------|
| Python 3.12+ | 后端 | 已有 |
| uv | Python 包管理 | 已有 |
| Node.js 18+ | 前端 | 已有 |
| Docker | Neo4j / Hermes / ASR | 已有 |
| Neo4j 5.x | 知识图谱 | `docker run -d --name neo4j -p 7474:7474 -p 7687:7687 -e NEO4J_AUTH=neo4j/<密码> neo4j:5-community` |
| Qwen3-ASR (可选) | B站视频转文字 | Docker (见下) |
| MediaCrawler | 知乎/小红书/微博 | `git clone https://github.com/NanmiCoder/MediaCrawler.git ~/Documents/MediaCrawler && cd ~/Documents/MediaCrawler && uv sync` |
| bilibili-cli | B站搜索/音频 | `uv tool install "bilibili-cli[audio]"` |
| bb-browser | 登录态 Chrome (CDP 9222) | `npm install -g bb-browser` |
| Hermes Docker (可选) | Plan 决策 + 写爬虫 | 见 `deploy/intel-planner/README.md` |

## 三、环境变量配置

```bash
cd ~/Documents/think_tank
cp .env.example .env
# 编辑 .env，至少填写：
#   SERPER_API_KEY   — Google 搜索 (https://serper.dev)
#   DEEPSEEK_API_KEY — LLM (https://platform.deepseek.com)
#   NEO4J_PASSWORD   — 与上面 docker run 一致
#   ZHIHU_COOKIE / XHS_COOKIE / WEIBO_COOKIE — 三平台登录 Cookie（可选，见下）
```

Cookie 获取：`bb-browser open https://www.zhihu.com` 打开独立 Chrome → 登录平台 →
F12 → Application → Cookies → 复制 Cookie 字符串填入 .env。
（MediaCrawler 默认 CDP 模式直接复用浏览器登录态，Cookie 是 fallback。）

## 四、启动服务

### 4.1 基础服务（Docker）

```bash
# Neo4j 知识图谱
docker start neo4j          # 首次: docker run -d --name neo4j -p 7474:7474 -p 7687:7687 -e NEO4J_AUTH=neo4j/<密码> neo4j:5-community

# Qwen3-ASR（可选，B站视频深度转录用）
docker start qwen3-asr      # 或按你的 docker-compose 启动

# Hermes Docker（可选，Plan 决策/动态工具生成，见 deploy/intel-planner/README.md）
cd deploy/intel-planner
docker compose up -d
```

### 4.2 登录态 Chrome（知乎/小红书/微博必需）

```bash
bb-browser open "https://www.zhihu.com"
# 在 bb-browser 弹出的 Chrome 中确认三平台登录态有效
# 验证: ss -tln | grep 9222 应监听
```

### 4.3 后端 (FastAPI)

```bash
cd ~/Documents/think_tank
uv sync
uv run uvicorn api.main:app --reload --port 8899
# → http://localhost:8899/health 应返回 {"status":"ok"}
```

### 4.4 前端 (Vue 3)

```bash
cd ~/Documents/think_tank/web
npm install
npm run dev -- --port 5173
# → 浏览器打开 http://localhost:5173
```

### 4.5 一键检查所有服务

```bash
# 浏览器左侧「📡 数据源状态」面板自动显示，或：
curl http://localhost:8899/api/health/providers          # 快速检测（配置/端口/进程）
curl "http://localhost:8899/api/health/providers?deep=true"  # 深度检测（实际搜索，慢）
```

## 五、使用指南

### 发起调查

1. 打开 http://localhost:5173
2. 左侧表单：**实体名称**（如 `蒋方舟`）+ **关注方向 hints**（如 `政治立场`）+ 深挖轮数
3. **Plan 决策模式**：
   - `auto`（推荐）：Hermes 生成计划，失败自动降级自研
   - `hermes`：强制 Docker Hermes（更聪明但慢）
   - `local`：只用自研（快）
4. **中途可调整方向**：开启后每轮暂停，可输入"别追争议了，专注资金链"

### 调查过程（实时观察）

- **调查计划**（右上）：Hermes/自研生成的维度模板（基线档案→资金链条→…）
- **调查进度**（左中）：每轮搜索/筛选/抽取/ASR 时间线
- **调查日志**（右下）：结构化日志实时刷新（LLM 调用/数据源结果/代码位置）
- **线索并行**：high 优先级线索自动 Send API 并行深挖（最多 3 条/轮）

### 报告与图谱

- 调查结束 → `待审阅` → 右侧显示完整报告（逻辑链路/发现/时间线/待验证）
- **批准** → 实体关系写入 Neo4j（实体归一化、关系证据累积）
- 图谱区：调跳数(1-3)、**单击节点**=深挖面板、**双击**=展开子图

### 动态工具（Phase C）

缺数据源时：左侧「🛠️ 动态工具」→ 输入需求 → 选审批模式 → 生成：
- `auto`：LLM 安全审查，通过自动注册，发现问题反馈 Hermes 修改（最多 3 轮）
- `manual`：人工审阅源码后批准

### 数据源状态

左侧「📡 数据源状态」面板：
- 绿点=正常 / 红点=异常（看消息定位，如"Chrome CDP(9222) 未启动"）
- **深度检测**：实际搜索验证登录态（慢，能发现端口通但登录失效的隐藏问题）

## 六、API 速查

```bash
POST /api/investigate          # 发起调查 {entity_name, hints, goal, max_rounds, plan_provider, adjustable, parent_*}
GET  /api/jobs                 # 任务列表
GET  /api/jobs/{id}            # 进度/状态（含 plan/leads/investigation_rounds）
GET  /api/jobs/{id}/report     # 报告（含 markdown）
GET  /api/jobs/{id}/logs       # 结构化日志（P4.1）
POST /api/jobs/{id}/approve    # 批准→建图谱
POST /api/jobs/{id}/reject     # 丢弃
POST /api/jobs/{id}/adjust     # 中途调整方向 {instruction}
POST /api/jobs/{id}/continue   # 暂停点继续
POST /api/jobs/{id}/stop       # 中途停止
GET  /api/tools                # 工具清单（16 工具）
GET  /api/tools/audit          # 工具审计（job_id 过滤回放）
POST /api/tools/dynamic/generate  # 动态工具生成 {requirement, approval_mode}
GET  /api/tools/dynamic        # 动态工具列表
GET  /api/graph/entity/{name}  # 实体状态（investigated 标记）
GET  /api/graph/subgraph/{name}?depth=N  # 子图查询
GET  /api/health/providers     # 数据源健康状态
```

## 七、数据目录

```
data/
├── raw/              # 每次搜索请求的原始存档 JSONL
│   ├── search/       #   统一存档（全部源）
│   ├── zhihu/ xhs/ weibo/   #   MediaCrawler 平台存档
│   └── bilibili/{bvid}/     #   B站视频音频+ASR转录持久化
│       ├── audio/*.wav      #   原始片段 + 送 ASR 合并块
│       ├── transcript.txt   #   ASR 转录全文
│       └── meta.json        #   bvid/标题/作者/来源调查
├── investigations/   # 调查任务 JSON（HITL 状态机）
├── logs/             # 结构化调查日志（file:line:func 定位）
├── audit/            # 工具调用审计（跨进程回放）
├── checkpoints.db    # LangGraph checkpoint（断点续跑）
└── plans_compare/    # P0.3 双路线 plan 对比基准
```

## 八、常见问题

| 症状 | 原因 | 解决 |
|------|------|------|
| 知乎/小红书/微博返回 0 条 | Chrome CDP(9222) 没起 / 登录失效 | `bb-browser open <url>` 重新拉起并登录，数据源面板深度检测确认 |
| 小红书 0 条（端口通） | 平台风控/登录态失效 | 在 bb-browser 重新扫码登录小红书 |
| Hermes 生成动态工具超时 | 写代码慢 | 检查 `HERMES_POLL_TIMEOUT`（默认 300s），或 `docker exec hermes-intel-planner ls /opt/tools_dynamic/` |
| Plan 一直用 local | Hermes 容器没起 | `docker compose up -d`（deploy/intel-planner/） |
| B站视频转录失败 | Qwen3-ASR 没起 | `docker start qwen3-asr` |
| 调查很慢（几轮 5-15 分钟） | MediaCrawler 慢 + 多轮深挖 | 减少轮数；high 线索并行已优化（3 条并行） |

## 九、相关文档

- `.hermes/plans/2026-07-20_intelligence-system-architecture.md` — 系统架构
- `.hermes/plans/2026-07-20_data-source-inventory.md` — 数据源清单
- `.hermes/plans/2026-08-05_future-roadmap.md` — 开发路线图
- `.hermes/plans/2026-08-06_p03-plan-provider-fusion.md` — Plan 融合方案
- `.hermes/plans/2026-08-14_p42-graph-recursion.md` — 图谱递归扩展设计
- `deploy/intel-planner/README.md` — Hermes Docker 部署
- `entity_intel/INVESTIGATION_METHODOLOGY.md` — 调查方法论（大脑）

---

*开发分支: dev · 仓库: git@github.com:Flywolfs/thinktank.git*

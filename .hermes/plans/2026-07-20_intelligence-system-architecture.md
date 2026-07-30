# 智库情报系统 — 架构设计文档

> **状态：** 架构设计阶段，待讨论确认后进入实现计划
> **版本：** v1.0
> **日期：** 2026-07-20

---

## 目录

1. [核心理念](#1-核心理念)
2. [总体架构](#2-总体架构)
3. [子系统一：实体情报](#3-子系统一实体情报)
4. [子系统二：社会情报](#4-子系统二社会情报)
5. [子系统三：金融情报](#5-子系统三金融情报)
6. [共享基础设施](#6-共享基础设施)
7. [数据模型](#7-数据模型)
8. [技术选型](#8-技术选型)
9. [部署架构](#9-部署架构)
10. [分阶段实施路线](#10-分阶段实施路线)

---

## 1. 核心理念

### 设计原则

| 原则 | 含义 |
|------|------|
| **图即真相** | Neo4j 知识图谱是三个子系统的统一数据底座，实体和关系是第一公民 |
| **采集与分析分离** | 数据采集层只负责"拿回来"，不关心"怎么用"；分析层只消费标准化数据 |
| **LLM 是分析引擎，不是聊天窗口** | LLM 用于实体抽取、关系推理、摘要生成、重要性评分，而非对话界面 |
| **时间是一等维度** | 所有数据带时间戳，时间线是情报分析的基本视角 |
| **人机协作** | 系统自动化采集+分析，但最终判断和投注决策由人做出 |
| **渐进式构建** | 先让一个子系统跑通端到端，再扩展其余 |

### 三个子系统的关系

```
                   ┌──────────────────┐
                   │   社会情报        │
                   │  (舆情/热点/趋势)  │
                   └────────┬─────────┘
                            │ 事件/情绪信号
                   ┌────────▼─────────┐
  ┌────────────┐   │   知识图谱        │   ┌────────────┐
  │  实体情报   │───│   (Neo4j)        │───│  金融情报   │
  │ (人物/公司) │   │  统一数据底座     │   │ (投资/回测) │
  └────────────┘   └──────────────────┘   └────────────┘
       │                    │                     │
       └────────────────────┼─────────────────────┘
                            │
                   ┌────────▼─────────┐
                   │   采集层          │
                   │ RSSHub/AKShare/  │
                   │ MediaCrawler/    │
                   │ SpiderFoot/自研  │
                   └──────────────────┘
```

**关键洞察：** 三个子系统不是独立孤岛。社会情报发现的热点事件，会成为实体情报中的人物关系链条；实体情报挖掘出的公司关联，会成为金融情报中的投资信号；金融情报的回测结果，反过来校准社会情报的重要性评分权重。

---

## 2. 总体架构

### 五层架构

```
┌──────────────────────────────────────────────────────────────────┐
│                     5. 呈现层 (Presentation)                      │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐  │
│  │ 仪表板   │  │ 时间线   │  │ 图谱可视化│  │ 报告/邮件推送    │  │
│  │ Dashboard│  │ Timeline │  │Graph Viz │  │ Report Generator │  │
│  └──────────┘  └──────────┘  └──────────┘  └──────────────────┘  │
├──────────────────────────────────────────────────────────────────┤
│                     4. 分析层 (Analysis)                          │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐  │
│  │NER实体   │  │ 关系推理 │  │ 情感分析 │  │ 重要性评分       │  │
│  │Extractor │  │Relation  │  │Sentiment │  │ Importance       │  │
│  └──────────┘  └──────────┘  └──────────┘  └──────────────────┘  │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐  │
│  │ 摘要生成 │  │ 聚类分析 │  │ 趋势预测 │  │ 策略回测         │  │
│  │Summarizer│  │Clustering│  │TrendPred │  │ Backtest         │  │
│  └──────────┘  └──────────┘  └──────────┘  └──────────────────┘  │
│                       LLM Orchestrator                            │
├──────────────────────────────────────────────────────────────────┤
│                     3. 存储层 (Storage)                           │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐  │
│  │ Neo4j    │  │PostgreSQL│  │  Redis   │  │  MinIO / 本地    │  │
│  │ 知识图谱 │  │ 结构化数据│  │ 缓存/队列│  │  文件存储        │  │
│  └──────────┘  └──────────┘  └──────────┘  └──────────────────┘  │
├──────────────────────────────────────────────────────────────────┤
│                    3½. 处理层 (Processing)                        │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐  │
│  │ 去重     │  │ NER      │  │ 分类     │  │ 标准化/清洗      │  │
│  │Dedup     │  │实体识别  │  │Classifier│  │  Normalizer      │  │
│  └──────────┘  └──────────┘  └──────────┘  └──────────────────┘  │
├──────────────────────────────────────────────────────────────────┤
│                     2. 采集层 (Collection)                        │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐  │
│  │ RSSHub   │  │MediaCraw │  │ AKShare  │  │  自研采集器      │  │
│  │ (RSS聚合)│  │(社交爬虫)│  │(金融数据)│  │  Custom Crawlers │  │
│  └──────────┘  └──────────┘  └──────────┘  └──────────────────┘  │
│  ┌──────────┐  ┌──────────┐  ┌──────────────────────────────────┐  │
│  │SpiderFoot│  │ 搜索API  │  │  bb-browser (复杂网站自动化)     │  │
│  │(OSINT)   │  │(Bing/DDG)│  │                                  │  │
│  └──────────┘  └──────────┘  └──────────────────────────────────┘  │
├──────────────────────────────────────────────────────────────────┤
│                  1. 调度 & 编排层 (Orchestration)                  │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐  │
│  │  Cron    │  │ 事件触发 │  │ 任务队列 │  │  Hermes Kanban   │  │
│  │ 定时任务 │  │Webhook   │  │(Redis/Q) │  │  (多Agent协作)   │  │
│  └──────────┘  └──────────┘  └──────────┘  └──────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
```

### 数据流（端到端）

```
采集 → 原始存储 → 去重 → 标准化 → 实体抽取 → 关系推理 → 图谱存储 → 分析查询 → 呈现
  │                                                         │
  └─────────────────── 反馈循环 ────────────────────────────┘
         (分析结果触发新的采集，如"发现新实体→深挖")
```

---

## 3. 子系统一：实体情报

### 3.1 核心流程

```
用户输入: "雷军" + 深度=2
        │
        ▼
┌─────────────────────────────────────────────┐
│  Phase 1: 多源搜索                          │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐       │
│  │搜索API  │ │社交平台 │ │企业信息 │ ...   │
│  │Bing/DDG │ │Weibo/ZH │ │ENScan   │       │
│  └─────────┘ └─────────┘ └─────────┘       │
│  输出: 原始文本/网页列表                     │
└─────────────────────┬───────────────────────┘
                      ▼
┌─────────────────────────────────────────────┐
│  Phase 2: 实体抽取 (NER + LLM)              │
│  "雷军于2010年创立小米，林斌是联合创始人"     │
│  抽取:                                      │
│    Person: 雷军, 林斌                        │
│    Org: 小米                                 │
│    Event: 创立公司                           │
│    Date: 2010                                │
│    Relation: 雷军 -[创立]→ 小米               │
│    Relation: 雷军 -[联合创始人]→ 林斌          │
│    Relation: 林斌 -[任职于]→ 小米              │
└─────────────────────┬───────────────────────┘
                      ▼
┌─────────────────────────────────────────────┐
│  Phase 3: 去重 & 融合                        │
│  同一实体多源出现 → 合并为一个节点            │
│  "雷军" = "Lei Jun" = "雷布斯" → 同一节点    │
│  多个来源描述同一关系 → 增加证据权重           │
└─────────────────────┬───────────────────────┘
                      ▼
┌─────────────────────────────────────────────┐
│  Phase 4: 深度控制                          │
│  depth=2:                                   │
│    第1层: 雷军 → 小米, 林斌, 金山, 顺为...   │
│    第2层: 小米 → 黎万强, 洪锋, 高通...       │
│           林斌 → 谷歌, 微软...                │
│           金山 → 求伯君, 张旋龙...            │
│           顺为 → 许达来, 投资组合公司...      │
│  如果 depth=1: 只到第1层就停止               │
└─────────────────────┬───────────────────────┘
                      ▼
┌─────────────────────────────────────────────┐
│  Phase 5: 图谱存储 + 可视化                  │
│  所有节点+关系写入 Neo4j                     │
│  时间线自动生成（按事件的 date 排序）          │
└─────────────────────────────────────────────┘
```

### 3.2 数据模型（Neo4j 图 Schema）

```cypher
// 节点类型
(:Entity {id, name, type, aliases, description, first_seen, last_updated})
  - type ∈ {Person, Organization, Event, Location, Product, Topic, Document}

// Person 特有属性
(:Entity:Person {nationality, positions, industries})

// Organization 特有属性  
(:Entity:Organization {industry, location, parent_org, founded, ticker})

// Event 特有属性
(:Entity:Event {date, location, category, significance})

// 关系类型（带时间属性）
(:Entity)-[:FOUNDED {date, evidence_url}]->(:Entity)
(:Entity)-[:WORKS_AT {role, start_date, end_date, evidence_url}]->(:Entity)
(:Entity)-[:INVESTED_IN {amount, date, round, evidence_url}]->(:Entity)
(:Entity)-[:PARTNERED_WITH {scope, date, evidence_url}]->(:Entity)
(:Entity)-[:COMPETES_WITH {market, evidence_url}]->(:Entity)
(:Entity)-[:SUPPLIES_TO {product, evidence_url}]->(:Entity)
(:Entity)-[:MENTIONED_IN {context, sentiment, date, source_url}]->(:Document)
(:Entity)-[:RELATED_TO {strength, reason, evidence_url}]->(:Entity)
(:Entity)-[:LOCATED_IN]->(:Entity) // → Location
(:Entity)-[:PARTICIPATED_IN {role}]->(:Entity) // → Event
(:Event)-[:PRECEDES {gap_days}]->(:Event) // 事件时间线
(:Event)-[:CAUSED {confidence}]->(:Event) // 因果关系

// 索引
CREATE INDEX entity_name IF NOT EXISTS FOR (e:Entity) ON (e.name)
CREATE INDEX entity_type IF NOT EXISTS FOR (e:Entity) ON (e.type)
```

### 3.3 深度搜索算法

```python
def entity_search(
    core_entity: str,
    max_depth: int = 1,
    entity_types: list[str] = None  # 可选过滤：只追踪 Person/Organization
) -> GraphResult:
    """
    递归多源搜索，构建实体关系图谱
    
    Args:
        core_entity: 起始实体名称（人名、公司名等）
        max_depth: 搜索深度，1=只显示直接关联，2+=递归深挖
        entity_types: 限定追踪的实体类型，None=全部
    
    Returns:
        图谱数据 + 时间线
    """
    visited = set()           # 已访问实体，防止环路
    graph = KnowledgeGraph()  # 内存中的图结构
    
    def search(entity_name: str, depth: int):
        if depth > max_depth or entity_name in visited:
            return
        visited.add(entity_name)
        
        # 1. 多源搜索
        raw_results = multi_source_search(entity_name)
        
        # 2. LLM 实体抽取 + 关系推理
        extracted = llm_extract_entities_and_relations(
            entity_name, raw_results
        )
        
        # 3. 去重融合
        for entity in extracted.entities:
            if entity_types and entity.type not in entity_types:
                continue
            canonical = dedup_and_merge(entity)
            graph.add_node(canonical)
            graph.add_edge(entity_name, canonical.id, entity.relation)
        
        # 4. 递归深挖
        if depth < max_depth:
            for related in extracted.entities:
                search(related.name, depth + 1)
    
    search(core_entity, depth=1)
    return graph.to_result()
```

### 3.4 搜索源配置

```yaml
entity_search_sources:
  web:
    - provider: bing_search_api
      weight: 1.0
      description: "通用网页搜索"
    - provider: duckduckgo
      weight: 0.8
      description: "隐私搜索，备用"
  
  social:
    - provider: mediacrawler_weibo
      weight: 0.9
      description: "微博用户/关键词搜索"
    - provider: mediacrawler_zhihu
      weight: 0.7
      description: "知乎问答搜索"
    - provider: rsshub_weibo_keyword
      weight: 0.6
      description: "微博关键词 RSS"
  
  enterprise:
    - provider: enscan_go
      weight: 1.0
      description: "中国企业信息（天眼查/爱企查/ICP备案）"
  
  knowledge:
    - provider: wikipedia_api
      weight: 0.9
      description: "维基百科结构化知识"
    - provider: wikidata_api
      weight: 0.8
      description: "Wikidata 知识图谱"
    - provider: baidu_baike
      weight: 0.7
      description: "百度百科"
  
  news:
    - provider: rsshub_news
      weight: 0.5
      description: "新闻 RSS 聚合"
    - provider: news_search_api
      weight: 0.8
      description: "新闻搜索引擎"

  # 深度控制: depth>=2 时启用
  deep_search_only:
    - provider: spiderfoot
      depth_min: 2
      description: "OSINT 自动化扫描（只在大深度时开启，避免过载）"
```

---

## 4. 子系统二：社会情报

### 4.1 核心流程

```
                     ┌──────────────┐
                     │   Cron 调度   │
                     │ (每5-30分钟)  │
                     └──────┬───────┘
                            ▼
┌──────────────────────────────────────────────────────────────┐
│  Step 1: 多源热榜采集                                         │
│  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐     │
│  │ 微博 │ │ 百度 │ │ 知乎 │ │ 抖音 │ │ B站  │ │ 头条 │ ... │
│  │热搜榜│ │热搜榜│ │ 热榜 │ │ 热点 │ │ 热门 │ │ 热榜 │     │
│  └──┬───┘ └──┬───┘ └──┬───┘ └──┬───┘ └──┬───┘ └──┬───┘     │
│     └────────┴────────┴────────┴────────┴────────┘          │
│                         ▼                                     │
│              原始条目池 (raw_items)                            │
└──────────────────────────────────────────────────────────────┘
                            ▼
┌──────────────────────────────────────────────────────────────┐
│  Step 2: 去重 + 聚类                                          │
│  - 标题相似度 > 0.85 → 合并为同一事件                          │
│  - 同一 URL → 合并                                            │
│  - 实体重叠度高 → 归入同一话题簇                                │
│                                                              │
│  输出: 去重后的事件列表 (events)                                │
└──────────────────────────────────────────────────────────────┘
                            ▼
┌──────────────────────────────────────────────────────────────┐
│  Step 3: 分类 + 重要性评分                                     │
│                                                              │
│  分类: politics / economy / tech / society / international /  │
│        military / health / environment / entertainment        │
│                                                              │
│  重要性 = f(                                                 │
│    跨平台提及数 × w1,      // 多少平台在讨论                    │
│    增长速度 × w2,          // 热度上升速度                      │
│    信源权威度 × w3,        // 谁在报道（新华社 > 自媒体）        │
│    情感强度 × w4,          // 评论情绪的激烈程度                 │
│    实体匹配度 × w5         // 是否涉及系统监控列表中的实体        │
│  )                                                           │
│                                                              │
│  输出: 排序后的事件列表 + 重要性分数                            │
└──────────────────────────────────────────────────────────────┘
                            ▼
┌──────────────────────────────────────────────────────────────┐
│  Step 4: LLM 生成报告                                         │
│  - 每日简报: Top 10 事件摘要 + 趋势箭头                        │
│  - 每周分析: 话题聚类 + 叙事变迁                                │
│  - 每月战略: 长期趋势 + 风险预警                                │
│                                                              │
│  输出: Markdown 报告 + HTML 邮件                               │
└──────────────────────────────────────────────────────────────┘
                            ▼
┌──────────────────────────────────────────────────────────────┐
│  Step 5: 趋势追踪 + 预测                                       │
│  - 事件生命周期: emerging → rising → peak → declining → dead  │
│  - 相似历史事件比对                                            │
│  - 预测: 明天可能的热点（基于 周期性 + 事件关联 + 种子信号）     │
│                                                              │
│  输出: 趋势报告 → 存储到 PostgreSQL                            │
└──────────────────────────────────────────────────────────────┘
```

### 4.2 热榜源配置

```yaml
hot_sources:
  # 高频采集 (每5分钟)
  high_frequency:
    - name: weibo_hot_search
      source: mediacrawler
      route: weibo/hot
      interval: 300
      
    - name: baidu_hot_search
      source: rsshub
      route: baidu/top
      interval: 300
      
    - name: zhihu_hotlist
      source: rsshub
      route: zhihu/hotlist
      interval: 300
  
  # 中频采集 (每15分钟)
  medium_frequency:
    - name: bilibili_hot
      source: rsshub
      route: bilibili/hot-search
      interval: 900
      
    - name: bilibili_popular
      source: rsshub
      route: bilibili/popular
      interval: 900
      
    - name: toutiao_hot
      source: trendradar
      interval: 900
  
  # 低频采集 (每30分钟)
  low_frequency:
    - name: douyin_hot
      source: mediacrawler
      interval: 1800
      
    - name: thepaper_hot
      source: rsshub
      route: thepaper/featured
      interval: 1800
      
    - name: cls_hot
      source: rsshub
      route: cls/hot
      interval: 1800
      
    - name: wallstreetcn_hot
      source: rsshub
      route: wallstreetcn/hot
      interval: 1800
```

### 4.3 重要性评分算法

```python
def calculate_importance(event: HotEvent) -> float:
    """
    综合计算事件重要性，返回 0-100 的分数
    
    各项权重可通过回测调整（金融情报的反馈循环）
    """
    score = 0.0
    
    # 1. 跨平台覆盖度 (max 30分)
    platform_count = len(event.source_platforms)
    score += min(platform_count * 5, 30)
    
    # 2. 热度增长速度 (max 25分)  
    # velocity = 过去1小时的增量 / 过去24小时的总量
    velocity = event.mentions_1h / max(event.mentions_24h, 1)
    score += min(velocity * 25, 25)
    
    # 3. 信源权威度 (max 20分)
    authority_map = {
        'xinhua': 10, 'people_daily': 10, 'cctv': 9,
        'caixin': 8, 'cls': 7, 'thepaper': 7,
        'weibo_verified': 5, 'zhihu_verified': 4,
        'weibo_normal': 2, 'self_media': 1
    }
    max_authority = max(
        authority_map.get(s.type, 1) for s in event.sources
    )
    score += max_authority * 2  # max 20
    
    # 4. 情感强度 (max 15分)
    # sentiment_intensity = |正面% - 负面%| + 愤怒情绪%
    intensity = abs(event.sentiment.positive - event.sentiment.negative)
    intensity += event.sentiment.anger
    score += min(intensity * 15, 15)
    
    # 5. 实体匹配度 (max 10分)
    # 是否涉及用户关注的实体列表
    matched_entities = event.entities & config.watchlist_entities
    score += min(len(matched_entities) * 3, 10)
    
    return round(score, 1)
```

### 4.4 报告模板

```markdown
# 每日情报简报 — {{date}}

## 📊 概览
- 采集事件: {{total_events}} 条
- 去重后: {{unique_events}} 条  
- 重要性 > 60: {{high_importance}} 条

---

## 🔴 高重要度事件 (≥70分)

### {{rank}}. {{event.title}} [{{event.score}}分]
- **来源:** {{event.source_platforms}}
- **分类:** {{event.category}}
- **情感:** 正面{{sentiment.pos}}% / 负面{{sentiment.neg}}% / 中性{{sentiment.neu}}%
- **趋势:** {{trend_arrow}} ({{velocity_desc}})
- **关联实体:** {{event.entities}}
- **摘要:** {{event.summary}}
- **相关历史事件:** {{similar_past_events}}

---

## 🟡 中重要度事件 (50-69分)

...

---

## 🔮 明日预测

基于历史模式+当前种子信号，以下话题可能在明天成为热点：

1. {{prediction_1}} (置信度: {{confidence}}%)
2. {{prediction_2}} (置信度: {{confidence}}%)
```

---

## 5. 子系统三：金融情报

### 5.1 核心流程

```
┌──────────────────────────────────────────────────────────────┐
│                    多源数据聚合                                │
│                                                              │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌─────────────┐  │
│  │ AKShare  │  │ 社会情报 │  │ 实体情报 │  │ 外部API     │  │
│  │ 行情/宏观│  │ 舆情/情绪│  │ 事件图谱│  │ 汇率/大宗   │  │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └──────┬──────┘  │
│       └──────────────┴─────────────┴───────────────┘         │
│                          ▼                                    │
│                   因子工厂 (Factor Engine)                     │
│                          │                                    │
│        ┌─────────────────┼─────────────────┐                 │
│        ▼                 ▼                  ▼                 │
│  ┌──────────┐  ┌──────────────┐  ┌──────────────┐           │
│  │ 宏观因子 │  │  市场因子    │  │  事件因子    │           │
│  │GDP/CPI/  │  │PE/PB/RSI/   │  │地缘/政策/    │           │
│  │PMI/M2/   │  │成交量/波幅   │  │关联交易/制裁 │           │
│  │利差/汇率 │  │              │  │              │           │
│  └────┬─────┘  └──────┬───────┘  └──────┬───────┘           │
│       └───────────────┼─────────────────┘                    │
│                       ▼                                       │
│               策略引擎 (Strategy Engine)                       │
│              ┌────────┴────────┐                              │
│              ▼                 ▼                              │
│        ┌──────────┐    ┌──────────────┐                       │
│        │ LLM生成  │    │  规则引擎    │                       │
│        │ 投资策略 │    │  量化策略    │                       │
│        └────┬─────┘    └──────┬───────┘                       │
│             └────────┬────────┘                               │
│                      ▼                                        │
│               回测引擎 (Backtest Engine)                        │
│           (历史数据 + 滑点 + 手续费 + 市场冲击)                 │
│                      │                                        │
│                      ▼                                        │
│               策略优化 → 输出报告                               │
└──────────────────────────────────────────────────────────────┘
```

### 5.2 因子体系

```yaml
factors:
  macro:  # 宏观经济因子
    growth:
      - gdp_growth_yoy        # GDP 同比
      - industrial_output     # 工业增加值
      - pmi_manufacturing     # 制造业 PMI
      - pmi_services          # 服务业 PMI
    
    inflation:
      - cpi_yoy               # CPI 同比
      - ppi_yoy               # PPI 同比
    
    monetary:
      - m2_growth             # M2 增速
      - social_financing      # 社融增量
      - lpr_1y                # 1年期 LPR
      - lpr_5y                # 5年期 LPR
      - reserve_ratio         # 存款准备金率
    
    external:
      - cny_usd               # 人民币兑美元
      - cny_basket            # 人民币汇率指数
      - foreign_reserves      # 外汇储备
      - trade_balance         # 贸易差额
  
  market:  # 市场因子（以 A 股为例）
    fundamental:
      - pe_ttm                # 市盈率 TTM
      - pb                    # 市净率
      - roe                   # 净资产收益率
      - revenue_growth        # 营收增速
      - debt_ratio            # 资产负债率
      - dividend_yield        # 股息率
    
    technical:
      - ma_cross_50_200       # 均线金叉/死叉
      - rsi_14                # RSI
      - volume_anomaly        # 成交量异常
      - volatility_20d        # 20日波动率
      - northbound_flow       # 北向资金流向
    
    sentiment:
      - margin_balance        # 融资余额
      - new_accounts          # 新开户数
      - fund_flow             # 公募基金申赎
      - analyst_rating        # 分析师评级变化
  
  event:  # 事件因子（来自另外两个子系统）
    geopolitical:
      - conflict_index        # 地缘冲突指数
      - sanction_events       # 制裁事件
      - policy_change_signals # 政策转向信号
    
    entity_chain:
      - supply_chain_events   # 供应链事件
      - management_change     # 管理层变动
      - regulatory_action     # 监管动作
      - partnership_news      # 合作/投资新闻
```

### 5.3 回测引擎设计

```python
class BacktestEngine:
    """
    基于历史数据的策略回测
    
    必须考虑:
    - 滑点: 限价单 0%, 市价单 0.1%
    - 手续费: A股 万2.5 (买+卖), 印花税 千1 (卖)
    - 市场冲击: 大单交易对价格的冲击
    - 涨跌停限制: A股 ±10%
    - T+1 制度: 当日买入次日才能卖出
    """
    
    def run(
        self,
        strategy: Strategy,
        start_date: str,
        end_date: str,
        initial_capital: float = 100000,
        benchmark: str = "000300.SH"  # 沪深300
    ) -> BacktestResult:
        """
        返回:
        - 总收益率 / 年化收益率
        - 夏普比率 / 最大回撤 / 胜率
        - 每日持仓 / 交易记录
        - 与基准的对比
        - 因子贡献归因
        """
```

### 5.4 从其他子系统到金融信号的映射

| 情报信号 | 金融含义 | 影响的资产类别 |
|---------|---------|--------------|
| 实体情报: 某公司高管密集离职 | 公司治理风险 ↑ | 该公司股票/债券 |
| 实体情报: 某公司与新供应商签约 | 供应链重组机会 | 上下游公司股票 |
| 社会情报: "芯片" 话题热度飙升 200% | 半导体板块关注度 ↑ | 半导体 ETF/个股 |
| 社会情报: 某省政策文件被高频转发 | 地方政策利好 | 区域概念股 |
| 社会情报: 负面情绪集中在某行业 | 行业承压信号 | 该行业期货/股票 |
| 实体情报: 某关键人物出访某国 | 外交关系信号 | 汇率/大宗商品 |

---

## 6. 共享基础设施

### 6.1 Provider Pattern（复用 job_crawlers 的经验）

```
think_tank/
├── shared/                    # 公共库（三个子系统共用）
│   ├── __init__.py
│   ├── models/                # 统一数据模型
│   │   ├── entity.py          # Entity, Relation
│   │   ├── event.py           # HotEvent, NewsItem
│   │   ├── financial.py       # MarketData, Factor
│   │   └── report.py          # Report 模板
│   ├── storage/               # 统一存储接口
│   │   ├── neo4j_client.py    # 知识图谱读写
│   │   ├── postgres.py        # 结构化数据
│   │   ├── redis_client.py    # 缓存/队列
│   │   └── file_storage.py    # 文件存储
│   ├── nlp/                   # 共享 NLP 组件
│   │   ├── ner.py             # 实体识别
│   │   ├── sentiment.py       # 情感分析
│   │   ├── dedup.py           # 去重
│   │   └── classifier.py      # 分类器
│   ├── llm/                   # LLM 调用封装
│   │   ├── client.py          # 统一 LLM 接口
│   │   ├── extractor.py       # 实体/关系抽取 Prompt
│   │   ├── summarizer.py      # 摘要生成 Prompt
│   │   └── ranker.py          # 重要性评分 Prompt
│   ├── crawlers/              # 通用采集器基类
│   │   ├── base_crawler.py    # 基类（去重、限流、重试）
│   │   ├── rss_crawler.py     # RSS 采集适配器
│   │   ├── api_crawler.py     # API 采集适配器
│   │   └── browser_crawler.py # bb-browser 适配器
│   └── utils/
│       ├── config.py          # 统一配置
│       ├── logging.py         # 日志
│       └── scheduler.py       # 定时调度
│
├── entity_intel/              # 子系统一：实体情报
│   ├── __init__.py
│   ├── searcher.py            # 多源搜索编排
│   ├── graph_builder.py       # 图谱构建
│   ├── deep_search.py         # 递归深度搜索
│   ├── timeline.py            # 时间线生成
│   └── sources/               # 搜索源适配器
│       ├── web_search.py
│       ├── social_search.py
│       ├── enterprise_search.py
│       └── knowledge_base.py
│
├── social_intel/              # 子系统二：社会情报
│   ├── __init__.py
│   ├── hot_collector.py       # 热榜采集
│   ├── importance.py          # 重要性评分
│   ├── clusterer.py           # 事件聚类
│   ├── report_generator.py    # 报告生成
│   ├── trend_predictor.py     # 趋势预测
│   └── sources/               # 热榜源配置
│       └── hot_sources.yaml
│
├── financial_intel/           # 子系统三：金融情报
│   ├── __init__.py
│   ├── data_collector.py      # 金融数据采集
│   ├── factor_engine.py       # 因子计算
│   ├── strategy_engine.py     # 策略生成
│   ├── backtest_engine.py     # 回测引擎
│   └── portfolio.py           # 组合优化
│
├── api/                       # FastAPI 后端
│   ├── main.py
│   ├── routes/
│   │   ├── entity.py
│   │   ├── social.py
│   │   └── financial.py
│   └── middleware/
│
├── web/                       # Vue3 前端
│   ├── src/
│   │   ├── views/
│   │   │   ├── EntityGraph.vue    # 实体图谱可视化
│   │   │   ├── SocialDashboard.vue # 社会情报仪表板
│   │   │   └── FinancialBoard.vue  # 金融情报面板
│   │   └── components/
│   │       ├── GraphCanvas.vue     # 图谱渲染 (D3.js/vis.js)
│   │       ├── Timeline.vue        # 时间线组件
│   │       └── ReportCard.vue      # 报告卡片
│
├── docker-compose.yml         # 整体部署
└── README.md
```

### 6.2 LLM 编排器

```
LLM Orchestrator (shared/llm/)
│
├── 任务路由
│   ├── 实体抽取: model=deepseek-v4-pro, temp=0.1, json_mode=true
│   ├── 关系推理: model=deepseek-v4-pro, temp=0.1, json_mode=true  
│   ├── 情感分析: model=deepseek-v4-flash, temp=0.0
│   ├── 摘要生成: model=deepseek-v4-flash, temp=0.3
│   ├── 重要性评分: model=deepseek-v4-flash, temp=0.0, json_mode=true
│   └── 投资策略: model=deepseek-v4-pro, temp=0.5 (需要创造性)
│
├── 成本控制
│   ├── 缓存: 相同输入 24h 内复用结果 (Redis)
│   ├── 批处理: 多条实体抽取合并为一次 API 调用
│   └── 降级: flash 模型用于低优先级任务
│
└── 质量控制
    ├── JSON schema 校验
    ├── 置信度阈值过滤
    └── 多轮交叉验证（对关键实体）
```

---

## 7. 数据模型

### 7.1 PostgreSQL 表设计

```sql
-- 原始采集条目（未处理）
CREATE TABLE raw_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source VARCHAR(50) NOT NULL,        -- 'rsshub', 'mediacrawler', 'akshare'
    source_route VARCHAR(200),          -- RSSHub 路由
    content_type VARCHAR(50),           -- 'hot_search', 'news', 'market_data', 'search_result'
    raw_content JSONB NOT NULL,         -- 原始数据
    fetched_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (source, source_route, content_type, fetched_at)
);

-- 处理后的事件（社会情报的核心表）
CREATE TABLE events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title VARCHAR(500) NOT NULL,
    summary TEXT,
    category VARCHAR(50),               -- politics/economy/tech/society/...
    importance_score REAL DEFAULT 0,
    first_seen TIMESTAMPTZ NOT NULL,
    last_updated TIMESTAMPTZ DEFAULT NOW(),
    peak_at TIMESTAMPTZ,                -- 热度峰值时间
    status VARCHAR(20) DEFAULT 'emerging', -- emerging/rising/peak/declining/dead
    sentiment JSONB,                    -- {positive, negative, neutral, anger, fear}
    source_platforms TEXT[],
    source_count INTEGER DEFAULT 0,
    related_entities TEXT[],            -- 关联的实体名称列表
    trend_velocity REAL DEFAULT 0       -- 热度变化速度
);

CREATE INDEX idx_events_importance ON events (importance_score DESC);
CREATE INDEX idx_events_category ON events (category);
CREATE INDEX idx_events_first_seen ON events (first_seen);

-- 事件时间序列数据（追踪热度变化）
CREATE TABLE event_timeseries (
    event_id UUID REFERENCES events(id),
    recorded_at TIMESTAMPTZ NOT NULL,
    mentions INTEGER,
    importance_score REAL,
    PRIMARY KEY (event_id, recorded_at)
);

-- 每日报告
CREATE TABLE reports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    report_type VARCHAR(20) NOT NULL,   -- 'daily', 'weekly', 'monthly'
    date DATE NOT NULL,
    content TEXT NOT NULL,              -- Markdown
    top_events JSONB,                   -- Top N 事件摘要
    predictions JSONB,                  -- 预测内容
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (report_type, date)
);

-- 金融因子数据
CREATE TABLE factor_data (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    factor_name VARCHAR(100) NOT NULL,
    symbol VARCHAR(20),                 -- 股票代码 (null for macro factors)
    date DATE NOT NULL,
    value REAL NOT NULL,
    source VARCHAR(50),
    UNIQUE (factor_name, symbol, date)
);

-- 回测记录
CREATE TABLE backtests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    strategy_name VARCHAR(100) NOT NULL,
    params JSONB NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    metrics JSONB NOT NULL,             -- 收益率、夏普、回撤等
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

### 7.2 Neo4j 图 Schema（见 3.2 节）

---

## 8. 技术选型

| 层次 | 技术 | 理由 |
|------|------|------|
| **知识图谱** | Neo4j Community | 图数据库事实标准，Cypher 查询语言强大，社区版免费 |
| **结构化存储** | PostgreSQL | 成熟可靠，JSONB 支持半结构化数据 |
| **缓存/队列** | Redis | 去重缓存 + LLM 结果缓存 + 任务队列 |
| **文件存储** | 本地文件系统 / MinIO | 报告 PDF、截图、原始 HTML 归档 |
| **RSS 聚合** | RSSHub (Docker 自部署) | 45k+ stars，1000+ 路由，chromium-bundled 镜像 |
| **社交爬虫** | MediaCrawler | 57k stars，覆盖小红书/抖音/B站/微博/知乎 |
| **金融数据** | AKShare | 21k stars，全品类中国金融数据，免费 |
| **OSINT** | SpiderFoot | 19.7k stars，200+ 模块自动扫描 |
| **企业信息** | ENScan_GO | 4.6k stars，天眼查/爱企查/ICP 聚合 |
| **浏览器自动化** | bb-browser | 已有经验，处理复杂反爬 |
| **LLM** | deepseek-v4-pro / flash | 已配置，pro 做推理 flash 做批量 |
| **后端** | FastAPI (Python) | 已有经验（job_crawlers），异步支持好 |
| **前端** | Vue 3 + Vite + Element Plus | 已有经验，D3.js 做图谱可视化 |
| **任务调度** | APScheduler + Cron | 轻量，不需要 Airflow 的复杂度 |
| **容器化** | Docker Compose | 统一编排所有服务 |
| **监控** | 日志文件 + 邮件告警 | 先简单，后续可上 Grafana |

---

## 9. 部署架构

```yaml
# docker-compose.yml 概览
services:
  neo4j:
    image: neo4j:5-community
    volumes:
      - neo4j_data:/data
      - neo4j_logs:/logs
    environment:
      NEO4J_AUTH: neo4j/${NEO4J_PASSWORD}
    ports:
      - "7474:7474"  # HTTP
      - "7687:7687"  # Bolt
  
  postgres:
    image: postgres:16-alpine
    volumes:
      - pg_data:/var/lib/postgresql/data
    environment:
      POSTGRES_DB: thinktank
      POSTGRES_USER: thinktank
      POSTGRES_PASSWORD: ${PG_PASSWORD}
    ports:
      - "5432:5432"
  
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
  
  rsshub:
    image: diygod/rsshub:chromium-bundled
    environment:
      CACHE_TYPE: redis
      REDIS_URL: redis://redis:6379/
      # B站 Cookie 配置
      BILIBILI_COOKIE_xxx: ${BILIBILI_COOKIE}
    ports:
      - "1200:1200"
  
  api:
    build: ./api
    depends_on: [neo4j, postgres, redis, rsshub]
    environment:
      NEO4J_URI: bolt://neo4j:7687
      DATABASE_URL: postgresql://thinktank:${PG_PASSWORD}@postgres/thinktank
      REDIS_URL: redis://redis:6379/
      RSSHUB_URL: http://rsshub:1200
      LLM_API_KEY: ${LLM_API_KEY}
    ports:
      - "8899:8899"
  
  web:
    build: ./web
    depends_on: [api]
    ports:
      - "5173:5173"
```

---

## 10. 分阶段实施路线

### Phase 0: 基础设施搭建 (Week 1-2)

```
目标: 让采集层和存储层跑起来

任务:
  □ Docker Compose 启动 Neo4j + PostgreSQL + Redis + RSSHub
  □ shared/models/ 定义核心数据模型
  □ shared/storage/ 封装数据库连接
  □ shared/crawlers/base_crawler.py 编写基类
  □ 验证: RSSHub → raw_items 表 → 能查到数据
```

### Phase 1: 社会情报 MVP (Week 3-5)

```
目标: 端到端跑通"采集 → 去重 → 评分 → 报告"

理由: 社会情报是三个子系统中最独立、最快见效的。
      不需要知识图谱，不需要复杂回测，输出就是一份邮件报告。

任务:
  □ social_intel/hot_collector.py 多源热榜采集
  □ shared/nlp/dedup.py 标题相似度去重
  □ shared/nlp/classifier.py LLM 分类
  □ social_intel/importance.py 重要性评分
  □ social_intel/report_generator.py 每日简报生成
  □ social_intel/trend_predictor.py 趋势追踪
  □ api/routes/social.py 后端接口
  □ web/views/SocialDashboard.vue 前端仪表板
  □ 验证: Cron 定时执行 → 生成今日简报 → 发邮件
```

### Phase 2: 实体情报 MVP (Week 6-9)

```
目标: 输入人名/公司名 → 返回知识图谱

理由: 依赖 Neo4j + LLM 实体抽取管道，复杂度较高。
      先实现 depth=1，再扩展递归搜索。

任务:
  □ shared/llm/extractor.py 实体/关系抽取 Prompt 调优
  □ entity_intel/searcher.py 多源搜索编排
  □ entity_intel/graph_builder.py 图谱构建 (depth=1)
  □ entity_intel/timeline.py 时间线生成
  □ entity_intel/sources/web_search.py 搜索源适配
  □ entity_intel/sources/social_search.py 社交源适配
  □ api/routes/entity.py 后端接口
  □ web/views/EntityGraph.vue 图谱可视化 (D3.js)
  □ 验证: 搜索 "雷军" → 返回图谱 → 可视化
  □ entity_intel/deep_search.py 深度搜索 (depth=N)
  □ 验证: 搜索 "雷军" depth=2 → 更大的图（检查环路 + 性能）
```

### Phase 3: 金融情报 MVP (Week 10-13)

```
目标: 跑通因子计算 + 回测管道

理由: 依赖前两个子系统的信号输入 + AKShare 数据。
      先做事件驱动策略回测，再做多因子模型。

任务:
  □ financial_intel/data_collector.py AKShare 对接
  □ financial_intel/factor_engine.py 因子计算管道
  □ shared/llm/ 增加策略生成 Prompt
  □ financial_intel/strategy_engine.py 策略生成
  □ financial_intel/backtest_engine.py 回测引擎
  □ api/routes/financial.py 后端接口
  □ web/views/FinancialBoard.vue 前端面板
  □ 验证: 生成策略 → 回测 → 查看夏普/回撤
```

### Phase 4: 系统整合 & 反馈闭环 (Week 14-16)

```
目标: 三个子系统通过知识图谱互联，形成反馈循环

任务:
  □ 社会情报的事件自动注入知识图谱（新实体触发实体情报搜索）
  □ 实体情报的关联事件自动成为社会情报的信号
  □ 金融回测结果反哺社会情报的重要性评分权重
  □ 统一仪表板: 一个页面看到三个子系统的概览
  □ 性能优化: 大批量采集的批处理、LLM 调用缓存
  □ 文档完善
```

---

## 附录：关键设计决策 & 待讨论问题

### A. 为什么用 Neo4j 而不是纯 PostgreSQL？

PostgreSQL 做图查询（递归 CTE）在深度 > 2 时性能急剧下降。实体情报的核心价值就在于"意外发现远距离关联"（比如 A 的大学同学 B 现在是 C 公司的 CEO，而 C 公司是 D 政策的主要受益者），这种多跳查询是图数据库的天然优势。

### B. LLM 成本预估

| 操作 | 模型 | 预估 token/次 | 日频次 | 月成本(估) |
|------|------|:---:|:---:|:---:|
| 实体抽取 | v4-pro | ~2K | 100次搜索×10结果=1000 | ~¥150 |
| 关系推理 | v4-pro | ~1K | 500次 | ~¥40 |
| 摘要生成 | v4-flash | ~1K | 50份报告×10条=500 | ~¥5 |
| 情感分析 | v4-flash | ~0.5K | 2000条 | ~¥10 |
| 重要性评分 | v4-flash | ~0.5K | 500条 | ~¥3 |
| 投资策略 | v4-pro | ~4K | 5次 | ~¥2 |
| **合计** | | | | **~¥210/月** |

### C. 待讨论问题

1. **深度搜索的环路控制：** 当 A→B→C→A 形成环路时，如何优雅停止？基于"已访问集合"还是"证据衰减因子"？

2. **中文实体消歧：** "小米" 是公司还是食物？需要上下文消歧，当前方案依赖 LLM，是否有更好的方法？

3. **社会情报的报告频率：** 每日 + 每周 + 每月，是否需要"突发事件即时推送"？

4. **金融情报的策略执行：** 系统只做分析和建议，还是连接券商 API 自动下单？（后者有合规风险）

5. **数据归档策略：** 原始网页 HTML 要不要长期保存？存多久？

6. **知识图谱初始化：** 要不要先导入 Wikidata / 百度百科作为基础知识层？

---

> **下一步：** 请审阅此架构，确认方向后，我将针对 Phase 1（基础设施 + 社会情报 MVP）编写详细的实现计划。

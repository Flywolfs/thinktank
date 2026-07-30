# 情报系统 — 采集层数据源清单 & 验证计划 v3

> **用途：** Phase 0 逐一验证，每个源拿到真实数据后落地为可复用代码
> **原则：** 一个数据源不通过验证，不上生产；通过一个，固化一个
> **日期：** 2026-07-20（v3: 新增付费数据源调研）

---

## 目录

1. [实体情报采集源](#1-实体情报采集源)
   - 1.1 通用网页搜索（免费 + 付费）
   - 1.2 社交媒体深度采集
   - 1.3 企业信息
   - 1.4 知识库
   - 1.5 新闻搜索
2. [社会情报采集源](#2-社会情报采集源)
3. [金融情报采集源](#3-金融情报采集源)
4. [视频→音频→ASR 管道](#4-视频音频asr-管道)
5. [代码组织规范](#5-代码组织规范)
6. [验证流程](#6-验证流程)
7. [付费数据服务调研（⭐新增）](#7-付费数据服务调研)

---

## 1. 实体情报采集源

> 用途：给定一个实体名（人名/公司名/事件名），从互联网搜集相关信息

### 1.1 通用网页搜索

| 编号 | 源 | 落地文件 | 采集内容 | 价格 | 备注 |
|------|-----|---------|---------|------|------|
| E-WEB-01 | **Serper** (Google) | `shared/crawlers/web_search.py` → `SerperProvider` | Google 搜索结果(title, url, snippet) | $0.30-1.00/千次 | 🔥 推荐首选：最快最便宜，AI Agent 标配 |
| E-WEB-02 | **SerpApi** (Baidu) | `shared/crawlers/web_search.py` → `SerpapiBaiduProvider` | 百度搜索结果 | $15/千次 | 少数支持百度的 SERP API，中文搜索刚需 |
| E-WEB-03 | **DataForSEO** (备用) | `shared/crawlers/web_search.py` → `DataForSEOProvider` | Google/Bing/Baidu 搜索 | $0.60/千次 | 真正的按量付费，无月费 |
| E-WEB-04 | **Bing Search API** (免费备用) | `shared/crawlers/web_search.py` → `BingSearchProvider` | 网页搜索结果 | 免费 1000次/月 | 免费兜底方案 |
| E-WEB-05 | **DuckDuckGo** (免费备用) | `shared/crawlers/web_search.py` → `DDGSearchProvider` | 同上 | 免费 | 可能触发验证码，仅备用 |

**决策：** Serper（Google）+ SerpApi（Baidu）双引擎。中文实体搜索必须走百度，Google 对中文实体覆盖不足。DataForSEO 作低成本备选。

### 1.2 社交媒体深度采集（⭐ 新增 — 三大平台）

> 对每个平台，不仅搜到内容链接，还要抓取**内容正文/评论/音频转文字**

#### 1.2.1 B站（Bilibili）

| 编号 | 源 | 落地文件 | 采集内容 | 采集方式 |
|------|-----|---------|---------|---------|
| E-BIL-01 | **B站视频搜索** | `shared/crawlers/bilibili.py` → `BilibiliVideoSearch` | 按实体名搜索视频列表（BV号、标题、UP主、播放量、时长、简介） | RSSHub `/bilibili/vsearch/:kw`（自部署 chromium-bundled） 或直接调 B 站搜索 API |
| E-BIL-02 | **B站音频下载** | `shared/crawlers/bilibili.py` → `BilibiliAudioDownload` | 根据 BV 号下载音频（m4a/aac） | bilix `--only-audio` |
| E-BIL-03 | **B站音频转文字** | `shared/asr/pipeline.py` → 走 ASR 管道 | 16kHz mono WAV → 文本 | Qwen3-ASR Docker（已有，localhost:8000） |
| E-BIL-04 | **B站视频评论** | `shared/crawlers/bilibili.py` → `BilibiliComments` | 视频的评论列表（按热度/时间排序） | RSSHub `/bilibili/video/reply/:aid` 或 B站 API |

**完整管道：**
```
实体名 → 搜索视频列表 → 筛选相关视频（LLM判断标题+简介相关性）
       → 下载音频(bilix) → ffmpeg转16kHz mono WAV → Qwen3-ASR → 文本
       → 文本存入实体搜索结果的 content 字段
       → 同时抓取评论做补充舆情
```

#### 1.2.2 知乎

| 编号 | 源 | 落地文件 | 采集内容 | 采集方式 |
|------|-----|---------|---------|---------|
| E-ZH-01 | **知乎搜索（综合）** | `shared/crawlers/zhihu.py` → `ZhihuSearch` | 按实体名搜索：问答、文章、专栏 | MediaCrawler 或 bb-browser |
| E-ZH-02 | **知乎回答正文** | `shared/crawlers/zhihu.py` → `ZhihuAnswerContent` | 单个回答的完整正文（含图片描述） | bb-browser 或 MediaCrawler |
| E-ZH-03 | **知乎文章正文** | `shared/crawlers/zhihu.py` → `ZhihuArticleContent` | 专栏文章完整正文 | bb-browser |
| E-ZH-04 | **知乎回答评论** | `shared/crawlers/zhihu.py` → `ZhihuComments` | 回答下的评论 | RSSHub 或 bb-browser |

**注意：** 知乎反爬较强，优先自部署 RSSHub + Cookie，备选 bb-browser。

#### 1.2.3 小红书

| 编号 | 源 | 落地文件 | 采集内容 | 采集方式 |
|------|-----|---------|---------|---------|
| E-RED-01 | **小红书搜索** | `shared/crawlers/xiaohongshu.py` → `XHSSearch` | 按实体名搜索笔记列表（标题、作者、点赞数、封面） | MediaCrawler |
| E-RED-02 | **小红书笔记正文** | `shared/crawlers/xiaohongshu.py` → `XHSNoteContent` | 笔记完整正文（文字+图片描述） | MediaCrawler |
| E-RED-03 | **小红书评论** | `shared/crawlers/xiaohongshu.py` → `XHSComments` | 笔记下的评论 | MediaCrawler |

**注意：** MediaCrawler 需要 Playwright + 小红书登录态。先用 bb-browser 登录一次保存 Cookie，后续 MediaCrawler 复用。

### 1.3 企业信息

| 编号 | 源 | 落地文件 | 采集内容 | 采集方式 | 配置要求 |
|------|-----|---------|---------|---------|---------|
| E-ENT-01 | **ENScan_GO** | `shared/crawlers/enterprise.py` → `ENScanProvider` | 企业工商信息（股东/高管/对外投资/ICP备案/小程序/公众号） | CLI 子进程调用 | **需要天眼查或爱企查 Cookie**，配置在 `~/.enscan/config.yaml` |
| E-ENT-02 | **国家企业信用信息公示系统** | `shared/crawlers/enterprise.py` → `GSXTProvider` | 企业基本登记信息（备选，无需登录） | bb-browser | 无 |

> **ENScan Cookie 配置方法：** 浏览器登录 [天眼查](https://www.tianyancha.com) 或 [爱企查](https://aiqicha.baidu.com)，F12 → Application → Cookies，复制完整 Cookie 字符串填入 `~/.enscan/config.yaml`。

### 1.4 知识库

| 编号 | 源 | 落地文件 | 采集内容 | 采集方式 |
|------|-----|---------|---------|---------|
| E-KB-01 | **Wikipedia** | `shared/crawlers/knowledge.py` → `WikipediaProvider` | 实体摘要、Infobox、分类 | `wikipedia` Python 库 |
| E-KB-02 | **Wikidata** | `shared/crawlers/knowledge.py` → `WikidataProvider` | 结构化关系（P属性） | SPARQL 查询 |
| E-KB-03 | **百度百科** | `shared/crawlers/knowledge.py` → `BaiduBaikeProvider` | 实体摘要、信息框 | bb-browser |

### 1.5 新闻搜索

| 编号 | 源 | 落地文件 | 采集内容 | 采集方式 |
|------|-----|---------|---------|---------|
| E-NEWS-01 | **Bing News Search** | `shared/crawlers/news_search.py` → `BingNewsProvider` | 包含实体名的近30天新闻 | API |

---

## 2. 社会情报采集源

> 用途：定时采集各平台热搜/热榜，生成热点报告

### 2.1 高频热榜（每5-10分钟）

| 编号 | 源 | 落地文件 | 采集内容 | 采集方式 |
|------|-----|---------|---------|---------|
| S-HOT-01 | **微博热搜** | `shared/crawlers/hot_sources.py` → `WeiboHotProvider` | 热搜标题、热度值 | RSSHub `/weibo/search/hot` |
| S-HOT-02 | **百度热搜** | `shared/crawlers/hot_sources.py` → `BaiduHotProvider` | 热搜标题、热度 | RSSHub（路由需确认） |
| S-HOT-03 | **知乎热榜** | `shared/crawlers/hot_sources.py` → `ZhihuHotProvider` | 热榜标题、热度、分类 | RSSHub `/zhihu/hotlist` |
| S-HOT-04 | **B站热搜** | `shared/crawlers/hot_sources.py` → `BilibiliHotProvider` | 热搜词、排名 | RSSHub `/bilibili/hot-search` |

### 2.2 中频热榜（每15-30分钟）

| 编号 | 源 | 落地文件 | 采集内容 | 采集方式 |
|------|-----|---------|---------|---------|
| S-HOT-05 | **B站热门** | `shared/crawlers/hot_sources.py` → `BilibiliPopularProvider` | 热门视频信息 | RSSHub `/bilibili/popular` |
| S-HOT-06 | **B站排行榜** | `shared/crawlers/hot_sources.py` → `BilibiliRankingProvider` | 三日排行榜 | RSSHub `/bilibili/ranking/0/3` |
| S-HOT-07 | **今日头条** | `shared/crawlers/hot_sources.py` → `ToutiaoHotProvider` | 头条热搜 | RSSHub（路由需确认） |
| S-HOT-08 | **抖音热点** | `shared/crawlers/hot_sources.py` → `DouyinHotProvider` | 抖音热门话题 | MediaCrawler |
| S-HOT-09 | **澎湃新闻** | `shared/crawlers/hot_sources.py` → `ThepaperHotProvider` | 热门文章 | RSSHub（路由需确认） |

### 2.3 低频财经/时政热榜（每30-60分钟）

| 编号 | 源 | 落地文件 | 采集内容 | 采集方式 |
|------|-----|---------|---------|---------|
| S-HOT-10 | **财联社** | `shared/crawlers/hot_sources.py` → `CLSHotProvider` | 实时财经快讯 | RSSHub（路由需确认） |
| S-HOT-11 | **华尔街见闻** | `shared/crawlers/hot_sources.py` → `WallstreetcnProvider` | 热门文章 | RSSHub（路由需确认） |
| S-HOT-12 | **36氪快讯** | `shared/crawlers/hot_sources.py` → `Kr36Provider` | 科技商业快讯 | RSSHub `/36kr/newsflashes` |

---

## 3. 金融情报采集源

> 全部通过 AKShare，一个入口

| 编号 | 源 | 落地文件 | 采集内容 |
|------|-----|---------|---------|
| F-MACRO-01 | GDP / CPI / PPI | `shared/crawlers/finance.py` → `MacroProvider` | 宏观经济指标时间序列 |
| F-MACRO-02 | PMI | 同上 | 制造业/服务业 PMI |
| F-MACRO-03 | M2 / 社融 | 同上 | M2增速/社融增量 |
| F-MACRO-04 | 利率 | 同上 | LPR / SHIBOR / 国债收益率 |
| F-MKT-01 | A股日线 | `shared/crawlers/finance.py` → `MarketProvider` | OHLCV + 换手率 |
| F-MKT-02 | A股实时行情 | 同上 | 实时价格/涨跌幅 |
| F-MKT-03 | A股财务数据 | 同上 | PE/PB/ROE/营收/负债 |
| F-MKT-04 | 沪深港通 | 同上 | 北向/南向资金 |
| F-MKT-05 | 融资融券 | 同上 | 融资余额 |
| F-MKT-06 | ETF数据 | 同上 | 净值/份额 |
| F-FX-01 | 人民币汇率 | `shared/crawlers/finance.py` → `FXProvider` | 主要货币对 |
| F-FX-02 | 美元指数 | 同上 | DXY |
| F-COMM-01 | 黄金/原油 | `shared/crawlers/finance.py` → `CommodityProvider` | 国际金价/原油 |
| F-COMM-02 | 国内期货 | 同上 | 沪铜/螺纹钢等 |

---

## 4. 视频→音频→ASR 管道

> 这是实体情报中 B站深度采集的核心基础设施

### 4.1 管道架构

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  视频URL/BV号 │ ──→ │  bilix 下载  │ ──→ │  ffmpeg 转码 │ ──→ │ Qwen3-ASR   │
│              │     │  --only-audio│     │ 16kHz mono   │     │ localhost:8000│
└──────────────┘     └──────────────┘     │  .wav         │     └──────┬───────┘
                                          └──────────────┘            │
                                                                      ▼
                                                              ┌──────────────┐
                                                              │  文本存入DB  │
                                                              └──────────────┘
```

### 4.2 关键参数 & 已知坑

| 步骤 | 工具 | 命令/配置 | 注意事项 |
|------|------|---------|---------|
| 下载音频 | bilix | `bilix get_video <url> --only-audio` | 已验证可用；yt-dlp 遇 412 反爬不可用 |
| 转码 | ffmpeg | `ffmpeg -i input.m4a -ar 16000 -ac 1 output.wav` | 必须 16kHz mono |
| 长音频切分 | ffmpeg | `ffmpeg -i input.wav -f segment -segment_time 240 -c copy chunk_%03d.wav` | 超过 10min 的视频切成 4min 片段，防止 ASR token 截断 |
| ASR | Qwen3-ASR | POST `localhost:8000/v1/audio/transcriptions` | Docker 运行中 (PID 18260)，已知 gpu-memory-utilization 过高（预占 20GB/22GB） |

### 4.3 落地文件

| 文件 | 职责 |
|------|------|
| `shared/asr/pipeline.py` | ASR 管道主控：下载→转码→切分→转录→合并 |
| `shared/asr/bilibili_downloader.py` | bilix 调用封装，支持批量 BV 号 |
| `shared/asr/transcriber.py` | Qwen3-ASR API 调用封装，含重试+超时 |

### 4.4 与实体搜索的集成方式

```python
# 伪代码：实体搜索 → B站视频 → ASR
class BilibiliDeepCollector:
    def collect(self, entity_name: str, max_videos: int = 10):
        # 1. 搜索视频
        videos = self.search_videos(entity_name, limit=max_videos)
        
        # 2. LLM 筛选：哪些视频真正与实体相关
        relevant = self.llm_filter(entity_name, videos)
        
        # 3. 下载音频 + ASR
        for video in relevant:
            audio_path = self.download_audio(video.bv_id)
            text = self.asr_pipeline.transcribe(audio_path)
            
            # 4. 文本 + 元信息存入结果
            yield {
                'source': 'bilibili',
                'url': video.url,
                'title': video.title,
                'author': video.author,
                'play_count': video.play_count,
                'content_text': text,    # ← ASR 转写结果
                'collected_at': datetime.now()
            }
```

---

## 5. 代码组织规范

### 5.1 每个数据源都是一个 Provider

采用已有的 Provider Pattern（复用 job_crawlers 经验）：

```
shared/crawlers/
├── __init__.py
├── base.py              # 基类：限流、重试、日志、结果标准化
├── web_search.py        # BingSearchProvider, DDGSearchProvider
├── bilibili.py          # BilibiliSearchProvider, BilibiliAudioProvider, BilibiliCommentProvider
├── zhihu.py             # ZhihuSearchProvider, ZhihuContentProvider, ZhihuCommentProvider
├── xiaohongshu.py       # XHSSearchProvider, XHSContentProvider, XHSCommentProvider
├── enterprise.py        # ENScanProvider, GSXTProvider
├── knowledge.py         # WikipediaProvider, WikidataProvider, BaiduBaikeProvider
├── news_search.py       # BingNewsProvider
├── hot_sources.py        # 所有 S-HOT-xx 的热榜 Provider
└── finance.py            # MacroProvider, MarketProvider, FXProvider, CommodityProvider

shared/asr/
├── __init__.py
├── pipeline.py          # ASR 管道主控
├── bilibili_downloader.py  # bilix 封装
└── transcriber.py       # Qwen3-ASR 调用封装
```

### 5.2 Provider 接口规范

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

@dataclass
class SearchParams:
    """统一的搜索参数，所有 Provider 共用"""
    query: str                             # 搜索关键词
    time_start: datetime | None = None     # 起始时间，None=不限制
    time_end: datetime | None = None       # 结束时间，None=当天
    max_results: int = 20                  # 最大返回条数
    extra: dict = field(default_factory=dict)  # 平台特有参数

@dataclass
class SearchResult:
    """所有采集器返回的统一数据结构"""
    source: str           # 数据源标识，如 'bilibili', 'zhihu', 'weibo_hot'
    source_type: str      # 'social', 'news', 'knowledge', 'enterprise', 'hot'
    url: str
    title: str
    content: str          # 正文/摘要/ASR转写文本
    author: str = ''
    published_at: datetime = None
    metadata: dict = field(default_factory=dict)  # 平台特有字段
    raw_html: str = ''    # 原始 HTML（可选，用于后续重新解析）

class BaseProvider(ABC):
    """所有采集 Provider 的基类"""
    
    @abstractmethod
    def search(self, params: SearchParams) -> list[SearchResult]:
        """搜索接口 — 每个 Provider 自行决定如何处理时间参数"""
        ...
    
    @abstractmethod
    def health_check(self) -> bool:
        """验证数据源是否可用"""
        ...
    
    def get_source_name(self) -> str:
        """返回数据源唯一标识"""
        raise NotImplementedError
    
    def supports_time_filter(self) -> bool:
        """
        声明此 Provider 是否原生支持时间过滤。
        用于上层分时段搜索策略判断是否需要调用 deep_time_search()。
        """
        return False
    
    def _filter_by_time(self, results: list[SearchResult], params: SearchParams) -> list[SearchResult]:
        """通用本地时间过滤，子类可选调用"""
        if not params.time_start and not params.time_end:
            return results
        start = params.time_start or datetime.min
        end = params.time_end or datetime.now()
        return [r for r in results
                if r.published_at and start <= r.published_at <= end]
```

### 5.3 时间过滤策略（按 Provider 类型）

| Provider 类型 | 时间参数行为 | `supports_time_filter()` |
|-------------|------------|:---:|
| **搜索引擎** (Serper/SerpApi/Bing) | 传给搜索 API 的 `tbs`/`dateRestrict` 参数 | ✅ True |
| **社交媒体** (知乎/B站/小红书/微博) | API 不支持时间过滤，搜回后本地按 `published_at` 筛 | ❌ False |
| **B站视频搜索** | API 支持按发布时间排序，不支持时间范围。本地过滤 | ❌ False |
| **知识库** (Wikipedia/Wikidata/BaiduBaike) | **忽略** `time_start/time_end`，不做任何过滤 | ❌ False |
| **企业信息** (ENScan) | **忽略**，企业信息是当前快照 | ❌ False |
| **新闻搜索** (BingNews) | 传给 API 的时间范围 | ✅ True |
| **热榜采集** (社会情报) | **不适用** — 热榜本身就是实时数据 | ❌ False |
| **金融数据** (AKShare) | 传给 API 的 `start_date`/`end_date` | ✅ True |

**核心原则：** `SearchParams` 只是"请求参数"，每个 Provider 自行决定如何处理。Wikipedia 和 ENScan 不理会时间字段，直接做正常查询，不会返回空结果。

### 5.4 分时段深度搜索策略

搜索引擎有已知问题：长时间窗口搜一个实体，返回的大多是最近的文章引用旧事件，而不是各时期的原始报道。解决方案是把时间窗口切成小块逐段搜：

```python
# shared/crawlers/strategies.py
from datetime import datetime, timedelta

def deep_time_search(
    provider: BaseProvider,
    entity_name: str,
    years_back: int = 5,
    max_per_window: int = 10
) -> list[SearchResult]:
    """
    分时段深度搜索：将长时间窗口切为逐年小块，逐块搜索
    
    适用场景：
    - 搜索引擎：避免"宽窗口只返回近期文章引用旧事"
    - 社交媒体：不支持时间过滤的源，通过 API 本身的分页+本地过滤
    
    不适用：
    - supports_time_filter()=False 且无 published_at 的源（知识库/企业信息）
    """
    all_results = []
    now = datetime.now()
    
    for year_offset in range(years_back, 0, -1):
        window_start = datetime(now.year - year_offset, 1, 1)
        window_end = datetime(now.year - year_offset + 1, 1, 1) if year_offset > 1 else now
        
        params = SearchParams(
            query=entity_name,
            time_start=window_start,
            time_end=window_end,
            max_results=max_per_window
        )
        chunk = provider.search(params)
        all_results.extend(chunk)
        
        # 去重（同一 URL 可能出现在多个窗口）
    
    seen_urls = set()
    unique = []
    for r in all_results:
        if r.url not in seen_urls:
            seen_urls.add(r.url)
            unique.append(r)
    
    return unique
```

仅在 `supports_time_filter()=True` 的 Provider 上自动触发此策略。社交媒体类 Provider 如果数据量大且有 `published_at`，也可以手动调用。

### 5.5 验证即测试

每个 Provider 落地后，必须包含一个 `__main__` 自检脚本：

```python
# shared/crawlers/bilibili.py 末尾
if __name__ == '__main__':
    provider = BilibiliSearchProvider()
    
    # Step 1: 健康检查
    assert provider.health_check(), "Bilibili provider unavailable"
    
    # Step 2: 搜索测试
    params = SearchParams(query="雷军", max_results=5)
    results = provider.search(params)
    assert len(results) > 0, "No results returned"
    
    # Step 3: 数据完整性检查
    for r in results:
        assert r.title, "Missing title"
        assert r.url, "Missing URL"
    
    print(f"✅ BilibiliSearchProvider: {len(results)} results")
    for r in results:
        print(f"   - {r.title[:50]}... ({r.url})")
```

---

## 6. 验证流程

### 6.1 每个源的验证 SOP

```
Step 0: 环境准备
  - RSSHub 自部署: docker run -d --name rsshub -p 1200:1200 diygod/rsshub:chromium-bundled
  - MediaCrawler 安装: git clone + pip install
  - AKShare 安装: pip install akshare
  - bilix 安装: 已有 (uv tool install bilix)
  - Qwen3-ASR: 已有 Docker (localhost:8000)
  - bb-browser: 已有

对每个 Provider:

Step 1: 实现 Provider 类 + health_check()
  → python3 shared/crawlers/xxx.py  # 运行自检

Step 2: 数据完整性
  → 返回结果是否包含 title/url/content？
  → 时效性: 数据是否是最新的？
  → 没有被反爬（验证码/空页/403）

Step 3: 稳定性
  → 连续请求 3 次，是否有频率限制？
  → 换代理/UA后是否恢复？

Step 4: 标记
  → 通过 ✅ 记录时间、返回格式示例
  → 失败 ❌ 记录原因、可能的修复方案
```

### 6.2 验证优先级

按对 Phase 1（实体情报）的重要性排序：

```
P0 (第一天验证 — 实体情报核心依赖):
  □ E-WEB-01: Serper (Google 搜索)
  □ E-WEB-02: SerpApi (百度搜索)
  □ E-BIL-01: B站视频搜索
  □ E-BIL-02: B站音频下载 (bilix)
  □ E-BIL-03: ASR 管道端到端 (bilix → ffmpeg → Qwen3-ASR)
  □ E-ZH-01: 知乎搜索
  □ E-RED-01: 小红书搜索
  □ E-ENT-01: ENScan_GO 企业信息
  □ E-KB-01: Wikipedia API
  □ E-KB-03: 百度百科

P1 (第二天验证 — 实体情报补充):
  □ E-BIL-04: B站评论
  □ E-ZH-02/03: 知乎正文抓取
  □ E-ZH-04: 知乎评论
  □ E-RED-02/03: 小红书正文+评论
  □ E-KB-02: Wikidata
  □ E-NEWS-01: BingNews

P2 (第三天验证 — 社会情报):
  □ S-HOT-01 ~ S-HOT-12: 全部热榜源

P3 (第四天验证 — 金融):
  □ F-xxxx: 全部 AKShare 金融数据源
```

### 6.3 汇总表（待验证过程中逐项填写）

| 编号 | Provider | 验证结果 | 稳定性 | 备注 |
|------|---------|:---:|:---:|------|
| E-WEB-01 | SerperProvider (Google) | ⬜ | ⬜ | 付费 |
| E-WEB-02 | SerpapiBaiduProvider (百度) | ⬜ | ⬜ | 付费 |
| E-WEB-03 | DataForSEOProvider (备用) | ⬜ | ⬜ | 付费 |
| E-WEB-04 | BingSearchProvider (免费) | ⬜ | ⬜ | |
| E-WEB-05 | DDGSearchProvider | ⬜ | ⬜ | |
| E-BIL-01 | BilibiliSearchProvider | ⬜ | ⬜ | |
| E-BIL-02 | BilibiliAudioProvider | ⬜ | ⬜ | bilix |
| E-BIL-03 | ASR Pipeline E2E | ⬜ | ⬜ | bilix→ffmpeg→Qwen3-ASR |
| E-BIL-04 | BilibiliCommentProvider | ⬜ | ⬜ | |
| E-ZH-01 | ZhihuSearchProvider | ⬜ | ⬜ | |
| E-ZH-02 | ZhihuAnswerProvider | ⬜ | ⬜ | |
| E-ZH-03 | ZhihuArticleProvider | ⬜ | ⬜ | |
| E-ZH-04 | ZhihuCommentProvider | ⬜ | ⬜ | |
| E-RED-01 | XHSSearchProvider | ⬜ | ⬜ | 需登录态 |
| E-RED-02 | XHSContentProvider | ⬜ | ⬜ | 需登录态 |
| E-RED-03 | XHSCommentProvider | ⬜ | ⬜ | 需登录态 |
| E-ENT-01 | ENScanProvider | ⬜ | ⬜ | 需天眼查/爱企查 Cookie |
| E-KB-01 | WikipediaProvider | ⬜ | ⬜ | |
| E-KB-02 | WikidataProvider | ⬜ | ⬜ | |
| E-KB-03 | BaiduBaikeProvider | ⬜ | ⬜ | bb-browser |
| E-NEWS-01 | BingNewsProvider | ⬜ | ⬜ | |
| S-HOT-01 | WeiboHotProvider | ⬜ | ⬜ | |
| S-HOT-02 | BaiduHotProvider | ⬜ | ⬜ | |
| S-HOT-03 | ZhihuHotProvider | ⬜ | ⬜ | |
| S-HOT-04 | BilibiliHotProvider | ⬜ | ⬜ | |
| S-HOT-05 | BilibiliPopularProvider | ⬜ | ⬜ | |
| S-HOT-06 | BilibiliRankingProvider | ⬜ | ⬜ | |
| S-HOT-07 | ToutiaoHotProvider | ⬜ | ⬜ | |
| S-HOT-08 | DouyinHotProvider | ⬜ | ⬜ | |
| S-HOT-09 | ThepaperHotProvider | ⬜ | ⬜ | |
| S-HOT-10 | CLSHotProvider | ⬜ | ⬜ | |
| S-HOT-11 | WallstreetcnProvider | ⬜ | ⬜ | |
| S-HOT-12 | Kr36Provider | ⬜ | ⬜ | |
| F-MACRO-01~04 | MacroProvider | ⬜ | ⬜ | |
| F-MKT-01~06 | MarketProvider | ⬜ | ⬜ | |
| F-FX-01~02 | FXProvider | ⬜ | ⬜ | |
| F-COMM-01~02 | CommodityProvider | ⬜ | ⬜ | |

---

> **待确认后，按 P0→P1→P2→P3 顺序开始验证，每通过一个源即落地为可运行代码。**

---

## 7. 付费数据服务调研（⭐新增）

> 调研时间：2026-07-20
> 原则：优先免费/开源，在以下情况考虑付费——(1)免费方案不稳定 (2)数据质量差距大 (3)开发维护成本高于采购成本

### 7.1 SERP API（搜索引擎结果）

| 服务 | 价格(/千次) | 支持引擎 | 免费层 | 月费起 | 亮点 | 风险 |
|------|:---:|------|:---:|:---:|------|------|
| **Serper** | $0.30-1.00 | Google only | 2500次/月 | $50 | 🏆 最快最便宜，AI Agent 首选 | 不支持百度 |
| **SerpApi** | $15 | Google, **Baidu**, Bing, Yandex, YouTube 等80+ | 250次/月 | $75 | 唯一广泛支持百度的 SERP API | 最贵，月费制 |
| **DataForSEO** | $0.60-2.00 | Google, Bing, Yandex | $1 额度 | 无月费 | 真正的按量付费，有 Baidu 待确认 | 响应较慢(标准队列~5min) |
| **Bright Data** | $1.50 | Google, **Baidu**, DuckDuckGo 等 | $5 额度 | 按量 | 成功率最高 98%，400M+ IP | 月费 $500+ 的企业版才划算 |
| **ScrapingBee** | ~$5 | Google | 1000 额度 | $49 | 同时支持通用爬虫 | 隐藏费用多(JS渲染×5-75倍) |

**推荐组合：** Serper（日常 Google 搜索）+ SerpApi（中文实体专属百度搜索）

### 7.2 LLM 联网搜索

| 服务 | 价格 | 接入方式 | 特色 |
|------|------|---------|------|
| **豆包 Web Search 插件**（火山引擎） | 联网搜索 0.004元/千次（月送2万次），边想边搜 0.5元/次 | API，搭在豆包大模型上 | 模型自己判断何时搜、搜什么、拆关键词；可限定搜索源（抖音/头条等） |
| **豆包搜索**（独立产品） | 按量计费 / 预付费套餐 | 独立搜索 API | 不绑定 LLM，拿结构化搜索结果 |
| **Kimi API 联网搜索** | 需确认 | API | 长上下文 + 联网搜索 |
| **DeepSeek API** | ❌ 不支持 | — | 仅网页版有联网搜索，API 暂无 |

**对情报系统的价值：** 豆包 Web Search 插件可以做成"智能搜索层"——你不写爬虫逻辑，给模型一个 prompt 让它自己搜、自己总结。适合实体情报中"搜一下 X 的最新消息"这种开放性搜索。

### 7.3 国内舆情监测平台（参考）

| 平台 | 优势 | 适合场景 | 价格 |
|------|------|---------|------|
| **新浪舆情通** | 微博数据天然优势，AI Agent 自动生成专报 | 微博重度舆情用户 | 企业级，未公开 |
| **清博智能** | 微信/微博多平台，高校市场经验 | 学术/媒体 | 同上 |
| **微舆情** | 微博微信专注，博主分析+话题追踪 | 社交媒体专项 | 同上 |

**结论：** 个人项目不买。年费数万起步，自建 RSSHub + MediaCrawler + 热榜采集管道可覆盖大部分需求。如果未来有客户买单再考虑。

### 7.4 Web Scraping 兜底服务

| 服务 | 价格 | 亮点 | 何时用 |
|------|------|------|------|
| **ScrapingBee** | $49/月(150K credit) | 通用爬虫，反爬绕过 | 自研爬虫遇到 Cloudflare 等反爬兜底 |
| **ScraperAPI** | $49/月(100K credit) | 同样，但成功率偏低(68%) | 预算选项（不推荐，成功率低于竞品） |
| **Bright Data Web Unlocker** | $500+/月 | 企业级反爬，400M+ IP | 只有当你真的爬不到数据且数据价值 > $500 时才用 |

**结论：** Phase 0-1 先用免费方案 + bb-browser。只有当某个关键数据源怎么也搞不定时，才考虑 ScrapingBee $49/月兜底。

### 7.5 付费采购建议（按优先级）

| 优先级 | 产品 | 用途 | 月费估计 | 何时开始 |
|:---:|------|------|:---:|------|
| 🔴 P0 | **Serper** | Google 搜索（实体情报核心） | $10-50 | Phase 0 验证通过后立即 |
| 🔴 P0 | **SerpApi** | 百度搜索（中文实体刚需） | $75 | Phase 0 验证通过后立即 |
| 🟡 P1 | **豆包 Web Search 插件** | LLM 自主联网搜索（智能搜索层） | 0.2元/次，月送2万 | Phase 1 实体情报深度搜索时 |
| 🟢 备用 | **ScrapingBee** | 反爬兜底 | $49起 | 仅当自研爬虫遇到无法绕过的反爬时 |

### 7.6 不要花钱的地方

- ❌ 国内舆情平台（太贵，自建够用）
- ❌ Bright Data 企业版（$500+/月，杀鸡用牛刀）
- ❌ AKShare 付费版（免费版已经够全）
- ❌ 天眼查/企查查付费 API（ENScan_GO + 免费额度够用）

---

> **下一步：** 确认采购清单后，将 Serper + SerpApi 加入 Phase 0 P0 验证队列，逐一测试 API 可用性和中文搜索质量。

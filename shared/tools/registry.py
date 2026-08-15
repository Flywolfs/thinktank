"""Tool Registry — 执行层工具注册表（P1.1）

把 7 个数据源 Provider + 4 个处理步骤包装成带元信息的工具：
- name / description / parameters / cost / category
- 工具清单可序列化 → 交给 LLM function calling 动态选择
- 每次工具调用留痕（tool_audit）

设计原则（来自架构）：Plan 决策层要聪明（LLM），Execute 执行层可复现、
可重复调用、可追踪。Tool Registry 是执行层的"可调用清单"。
"""

from __future__ import annotations

import json
import time
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from shared.utils.logger import get_current_logger


@dataclass
class ToolParam:
    name: str
    type: str            # string / int / boolean
    description: str
    required: bool = True
    default: Any = None


@dataclass
class ToolSpec:
    """工具元信息（给 LLM 看 / 序列化用）"""
    name: str
    description: str
    category: str        # crawler / extract / filter / synthesize / graph
    parameters: list[ToolParam] = field(default_factory=list)
    cost: str = "low"    # low / medium / high（调外部API / 子进程 / 本地计算）
    provider: str = ""   # 底层实现（如 serper / wikipedia / llm）
    enabled: bool = True

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "cost": self.cost,
            "provider": self.provider,
            "enabled": self.enabled,
            "parameters": [
                {
                    "name": p.name,
                    "type": p.type,
                    "description": p.description,
                    "required": p.required,
                }
                for p in self.parameters
            ],
        }


@dataclass
class ToolCallRecord:
    """工具调用留痕（P1.3 完整审计）"""
    tool: str
    params: dict
    job_id: str = ""                 # 所属调查任务（回放按 job 过滤）
    ts: float = field(default_factory=time.time)
    duration_s: float = 0.0
    status: str = "ok"           # ok / error
    summary: str = ""            # 结果摘要（一行的概述）
    output: list = field(default_factory=list)   # 输出明细（标题/URL 等，回放用）
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "tool": self.tool,
            "job_id": self.job_id,
            "params": {k: (v if not isinstance(v, (dict, list)) else "<obj>") for k, v in self.params.items()},
            "ts": self.ts,
            "duration_s": round(self.duration_s, 2),
            "status": self.status,
            "summary": self.summary[:500],
            "output": self.output[:20],
            "error": self.error[:300],
        }


class ToolRegistry:
    """工具注册表 — 注册/查询/调用/审计（P1.3 支持磁盘持久化回放）"""

    def __init__(self, audit_dir: str | Path | None = None):
        self._tools: dict[str, ToolSpec] = {}
        self._handlers: dict[str, Callable] = {}
        self._audit: list[ToolCallRecord] = []
        self._audit_dir: Path | None = None
        if audit_dir is not None:
            self._audit_dir = Path(audit_dir)
            self._audit_dir.mkdir(parents=True, exist_ok=True)

    # ── 注册 ───────────────────────────────────────────

    def register(self, spec: ToolSpec, handler: Callable) -> None:
        self._tools[spec.name] = spec
        self._handlers[spec.name] = handler

    # ── 查询 ───────────────────────────────────────────

    def list_tools(self, category: str | None = None, enabled_only: bool = True) -> list[dict]:
        """工具清单（LLM function calling 上下文用）"""
        out = []
        for spec in self._tools.values():
            if enabled_only and not spec.enabled:
                continue
            if category and spec.category != category:
                continue
            out.append(spec.to_dict())
        return out

    def get_spec(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    # ── 调用（带审计） ─────────────────────────────────

    def call(self, name: str, job_id: str = "", **params) -> Any:
        """调用工具：执行 handler + 记录审计（内存 + 磁盘持久化）。抛 ToolNotFoundError / 透传 handler 异常"""
        if name not in self._handlers:
            raise ToolNotFoundError(f"工具未注册: {name}")

        handler = self._handlers[name]
        spec = self._tools[name]
        record = ToolCallRecord(tool=name, params=params, job_id=job_id)
        t0 = time.time()
        logger = get_current_logger()

        try:
            result = handler(**params)
            record.duration_s = time.time() - t0
            record.summary = self._summarize(result)
            record.output = self._extract_output(result)
            record.status = "ok"
            if logger:
                logger.info(
                    "tool_call", f"工具调用成功: {name}",
                    detail=record.to_dict(),
                )
            return result
        except Exception as e:
            record.duration_s = time.time() - t0
            record.status = "error"
            record.error = str(e)
            if logger:
                logger.info(
                    "tool_call", f"工具调用失败: {name}: {e}",
                    detail=record.to_dict(),
                )
            raise
        finally:
            self._audit.append(record)
            self._persist(record)

    # ── 审计 ───────────────────────────────────────────

    def audit(self, limit: int = 50, job_id: str = "") -> list[dict]:
        """工具调用记录（完整回放）。优先读磁盘（跨进程/重启后仍可回放），
        磁盘缺失时回退内存。job_id 非空时只返回该任务的记录。"""
        records = self._audit
        if self._audit_dir is not None and job_id:
            records = self._load_from_disk(job_id)
        if job_id:
            records = [r for r in records if r.job_id == job_id]
        return [r.to_dict() for r in records[-limit:]]

    def clear_audit(self) -> None:
        self._audit = []

    # ── 内部 ───────────────────────────────────────────

    def _persist(self, record: ToolCallRecord) -> None:
        """把记录追加写入 data/audit/{job_id}.jsonl"""
        if self._audit_dir is None or not record.job_id:
            return
        try:
            path = self._audit_dir / f"{record.job_id}.jsonl"
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
        except Exception:
            pass  # 审计落盘失败不阻塞工具调用

    def _load_from_disk(self, job_id: str) -> list:
        """从磁盘读该 job 的审计记录（回放）"""
        assert self._audit_dir is not None
        path = self._audit_dir / f"{job_id}.jsonl"
        if not path.exists():
            return []
        try:
            out = []
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    out.append(ToolCallRecord(**json.loads(line)))
            return out
        except Exception:
            return []

    def _summarize(self, result: Any) -> str:
        """结果摘要（一行的概述）"""
        try:
            if hasattr(result, "total_results"):
                return f"{result.total_results} 条结果"
            if hasattr(result, "print_summary"):
                return str(result.print_summary())[:200]
            if isinstance(result, (list, tuple)):
                return f"{len(result)} 项"
            if isinstance(result, dict):
                return f"{len(result)} 键"
            return str(result)[:200]
        except Exception:
            return ""

    def _extract_output(self, result: Any) -> list:
        """提取输出明细（标题/URL 等，供回放查看"每个工具返回什么"）"""
        try:
            # EntityReport：web/social/knowledge 结果标题+URL
            if hasattr(result, "web_results") or hasattr(result, "social_results"):
                items = []
                for r in list(getattr(result, "web_results", []))[:6] + \
                         list(getattr(result, "social_results", []))[:6] + \
                         list(getattr(result, "knowledge_results", []))[:6]:
                    items.append({"title": (r.title or "")[:80], "url": (r.url or "")[:120]})
                return items
            # list[SearchResult] 或普通 list
            if isinstance(result, (list, tuple)):
                items = []
                for r in result[:6]:
                    if hasattr(r, "title"):
                        items.append({"title": (r.title or "")[:80], "url": (r.url or "")[:120]})
                    else:
                        items.append({"item": str(r)[:120]})
                return items
            # dict（如 graph_build 返回统计）
            if isinstance(result, dict):
                return [{k: str(v)[:100]} for k, v in list(result.items())[:6]]
            # AnalysisReport / 其他对象
            if hasattr(result, "key_findings"):
                return [{"finding": str(f)[:150]} for f in result.key_findings[:6]]
            return []
        except Exception:
            return []


class ToolNotFoundError(Exception):
    pass


# ── 全局单例 + 构建器 ─────────────────────────────────

_default_registry: ToolRegistry | None = None


def get_registry() -> ToolRegistry:
    """获取全局 Tool Registry（惰性构建，避免导入时初始化外部依赖）。
    P1.3: 默认启用 data/audit/ 磁盘持久化（跨进程可回放）"""
    global _default_registry
    if _default_registry is None:
        from shared.utils import config
        _project_root = Path(__file__).resolve().parent.parent.parent
        _default_registry = build_default_registry(
            audit_dir=_project_root / "data" / "audit"
        )
    return _default_registry


def build_default_registry(audit_dir: str | Path | None = None) -> ToolRegistry:
    """构建默认工具集：7 个爬虫 Provider + 处理步骤 + 图执行级聚合工具"""
    reg = ToolRegistry(audit_dir=audit_dir)
    _register_crawlers(reg)
    _register_processors(reg)
    _register_graph_tools(reg)
    return reg


def _register_graph_tools(reg: ToolRegistry) -> None:
    """图执行级聚合工具（P1.2：调查图节点通过 registry 调用，独立可调用+留痕）"""
    from entity_intel.searcher import EntitySearcher
    from shared.llm.extractor import EntityExtractor

    searcher = EntitySearcher()

    # 全源搜索（对应 searcher.search，含 MediaCrawler）
    reg.register(
        ToolSpec(
            name="search_all",
            description="全源聚合搜索：知识库+搜索引擎+B站+企业+知乎+小红书+微博，返回 EntityReport。",
            category="crawler", cost="high", provider="multi",
            parameters=[
                ToolParam("query", "string", "搜索关键词"),
                ToolParam("max_per_source", "int", "每源最多条数", required=False, default=10),
            ],
        ),
        lambda query, max_per_source=10: searcher.search(query, max_per_source=max_per_source),
    )

    # 快速搜索（跳过 MediaCrawler 慢源，用于一轮中后续 query）
    reg.register(
        ToolSpec(
            name="search_all_fast",
            description="快速聚合搜索：跳过知乎/小红书/微博（慢源），适合多关键词轮转。",
            category="crawler", cost="medium", provider="multi",
            parameters=[
                ToolParam("query", "string", "搜索关键词"),
                ToolParam("max_per_source", "int", "每源最多条数", required=False, default=10),
            ],
        ),
        lambda query, max_per_source=10: searcher.search_fast(query, max_per_source=max_per_source),
    )

    # 报告级相关性筛选（对应 filter_entity_report）
    def filter_report_handler(report, hints: str = "", goal: str = "", use_llm: bool = True):
        from entity_intel.relevance import filter_entity_report
        return filter_entity_report(report, hints=hints, goal=goal, use_llm=use_llm)

    reg.register(
        ToolSpec(
            name="filter_report",
            description="报告级相关性筛选：对 EntityReport 整体筛选，返回保留/丢弃统计。",
            category="filter", cost="medium", provider="rules+deepseek",
            parameters=[
                ToolParam("hints", "string", "用户关注方向", required=False, default=""),
                ToolParam("goal", "string", "调查意图", required=False, default=""),
            ],
        ),
        filter_report_handler,
    )

    # 报告级实体抽取（对应 extract_from_report）
    reg.register(
        ToolSpec(
            name="extract_report",
            description="报告级实体抽取：从 EntityReport 中提取实体/关系/证据（5元组），填充 related_entities。",
            category="extract", cost="medium", provider="deepseek",
            parameters=[],
        ),
        lambda report: EntityExtractor().extract_from_report(report),
    )

    # 知识图谱全量构建（对应 graph_build_node）
    def graph_build_handler(report, analysis, entity_name: str, parent_job_id: str = "",
                            parent_entity: str = "", parent_relation: str = ""):
        """P4.2 版图谱构建:
        1. 实体归一化（中文简体/英文lowercase/别名）→ 同名合并
        2. investigated 标记 + 别名增量学习
        3. 关系证据累积（复用旧 rel_type + new_finding）
        4. 血统边: 父调查实体 → 本实体（有 parent 时）
        """
        from shared.nlp.entity_normalize import get_normalizer
        from shared.storage.neo4j_client import Neo4jClient

        normalizer = get_normalizer()
        client = Neo4jClient()
        client.ensure_indexes()

        # 核心实体归一化 + 标记
        core_key = normalizer.normalize(entity_name)
        client.merge_entity(
            core_key, entity_type="person",
            summary=(analysis.entity_summary or "")[:500],
            source="investigation",
        )
        client.mark_investigated(core_key)
        if entity_name != core_key:
            client.add_alias(core_key, entity_name)

        # 关联实体归一化入库
        for e in (analysis.suggested_entities or []):
            name = e.get("name", "")
            if not name:
                continue
            key = normalizer.normalize(name)
            if not key:
                continue
            client.merge_entity(
                key, entity_type=e.get("type", ""), source="investigation"
            )
            if name != key:
                client.add_alias(key, name)

        # 关系（归一化后 merge，同向边证据累积）
        rel_count = 0
        for r in (analysis.suggested_relations or []):
            frm, to, rel = r.get("from", ""), r.get("to", ""), r.get("relation", "")
            if not (frm and to and rel):
                continue
            frm_key = normalizer.normalize(frm)
            to_key = normalizer.normalize(to)
            if not (frm_key and to_key):
                continue
            client.merge_relation(
                frm_key, to_key, rel, source="investigation",
                new_finding=(r.get("evidence", "") or r.get("rationale", "") or "")[:300],
            )
            rel_count += 1

        # 血统边（图谱递归深挖时）: 父实体 → 本实体
        if parent_entity:
            client.merge_relation(
                parent_entity, core_key, "调查关联", source="investigation",
                new_finding=f"从父调查 {parent_job_id or '?'} 深挖而来 ({parent_relation or '关联'})",
            )

        return {"entities": len(analysis.suggested_entities or []), "relations": rel_count}

    reg.register(
        ToolSpec(
            name="graph_build",
            description="知识图谱全量构建：实体+关系 merge 进 Neo4j，返回构建统计。",
            category="graph", cost="medium", provider="neo4j",
            parameters=[ToolParam("entity_name", "string", "核心实体名")],
        ),
        lambda entity_name, report=None, analysis=None, parent_job_id="", parent_entity="", parent_relation="": graph_build_handler(
            report, analysis, entity_name, parent_job_id=parent_job_id,
            parent_entity=parent_entity, parent_relation=parent_relation,
        ),
    )


def _register_crawlers(reg: ToolRegistry) -> None:
    """7 个数据源 Provider → 搜索工具"""
    from entity_intel.searcher import EntitySearcher

    searcher = EntitySearcher()

    def make_search_handler(provider_name: str):
        def handler(query: str, max_results: int = 10, time_start: str = "", time_end: str = ""):
            from datetime import datetime
            try:
                ts = datetime.fromisoformat(time_start) if time_start else None
            except ValueError:
                ts = None
            try:
                te = datetime.fromisoformat(time_end) if time_end else None
            except ValueError:
                te = None
            provider = searcher._get_providers().get(provider_name)
            if not provider:
                raise ToolNotFoundError(f"数据源不可用: {provider_name}")
            from shared.crawlers.base import SearchParams
            params = SearchParams(query=query, max_results=max_results, time_start=ts, time_end=te)
            return provider.search(params)
        return handler

    crawler_specs = [
        ToolSpec(
            name="search_wikipedia",
            description="搜索维基百科（知识库，中英双语）。适合建立实体基线档案、查生平/履历/背景。",
            category="crawler", cost="low", provider="wikipedia",
            parameters=[
                ToolParam("query", "string", "搜索关键词（实体名或概念）"),
                ToolParam("max_results", "int", "最多返回条数", required=False, default=10),
            ],
        ),
        ToolSpec(
            name="search_serper",
            description="Google 搜索（Serper API）。覆盖面最广，适合绝大多数搜索场景。",
            category="crawler", cost="medium", provider="serper",
            parameters=[
                ToolParam("query", "string", "搜索关键词"),
                ToolParam("max_results", "int", "最多返回条数", required=False, default=10),
                ToolParam("time_start", "string", "起始日期 ISO 格式 (YYYY-MM-DD)", required=False, default=""),
                ToolParam("time_end", "string", "结束日期 ISO 格式", required=False, default=""),
            ],
        ),
        ToolSpec(
            name="search_bilibili",
            description="搜索 B站 视频（bilibili-cli）。适合查视频/UP主/事件相关视频内容。",
            category="crawler", cost="medium", provider="bilibili-cli",
            parameters=[
                ToolParam("query", "string", "搜索关键词"),
                ToolParam("max_results", "int", "最多返回条数", required=False, default=10),
            ],
        ),
        ToolSpec(
            name="search_enterprise",
            description="中国企业信息（Serper 片段提取：法人/注册资本/信用代码）。适合查公司背景。",
            category="crawler", cost="medium", provider="serper+regex",
            parameters=[
                ToolParam("query", "string", "公司名或关键词"),
                ToolParam("max_results", "int", "最多返回条数", required=False, default=10),
            ],
        ),
        ToolSpec(
            name="search_zhihu",
            description="搜索知乎（MediaCrawler CDP）。适合查深度问答/个人言论。",
            category="crawler", cost="high", provider="mediacrawler",
            parameters=[
                ToolParam("query", "string", "搜索关键词"),
                ToolParam("max_results", "int", "最多返回条数", required=False, default=10),
            ],
        ),
        ToolSpec(
            name="search_xiaohongshu",
            description="搜索小红书（MediaCrawler CDP）。适合查生活方式/消费/品牌口碑。",
            category="crawler", cost="high", provider="mediacrawler",
            parameters=[
                ToolParam("query", "string", "搜索关键词"),
                ToolParam("max_results", "int", "最多返回条数", required=False, default=10),
            ],
        ),
        ToolSpec(
            name="search_weibo",
            description="搜索微博（MediaCrawler CDP）。适合查热点舆论/即时反应。",
            category="crawler", cost="high", provider="mediacrawler",
            parameters=[
                ToolParam("query", "string", "搜索关键词"),
                ToolParam("max_results", "int", "最多返回条数", required=False, default=10),
            ],
        ),
    ]

    for spec in crawler_specs:
        provider_name = spec.provider
        if provider_name == "mediacrawler":
            # 映射到 searcher 内部名称
            name_map = {"search_zhihu": "zhihu", "search_xiaohongshu": "xiaohongshu", "search_weibo": "weibo"}
            provider_name = name_map.get(spec.name, provider_name)
        elif provider_name == "serper+regex":
            provider_name = "enterprise"
        reg.register(spec, make_search_handler(provider_name))


def _register_processors(reg: ToolRegistry) -> None:
    """4 个处理步骤 → 独立可调用工具（P1.2 基础）"""
    from shared.llm.extractor import EntityExtractor
    from shared.nlp.relevance import RelevanceFilter

    # 实体抽取
    reg.register(
        ToolSpec(
            name="extract_entities",
            description="LLM 实体抽取：从搜索结果中提取实体/关系/证据/方向（5元组）。",
            category="extract", cost="medium", provider="deepseek",
            parameters=[
                ToolParam("text", "string", "要抽取的文本内容"),
                ToolParam("entity_name", "string", "核心实体名（调查对象）", required=False, default=""),
            ],
        ),
        lambda text, entity_name="": EntityExtractor().extract(text, entity_name=entity_name),
    )

    # 相关性打分
    def filter_handler(results: list, subject: str = "", hints: str = "", goal: str = "", use_llm: bool = True):
        return RelevanceFilter().filter_results(
            list(results), subject=subject, hints=hints, goal=goal, use_llm=use_llm
        )

    reg.register(
        ToolSpec(
            name="filter_relevance",
            description="相关性筛选：两阶段（规则粗筛+LLM精筛），返回保留/丢弃/线索。",
            category="filter", cost="medium", provider="rules+deepseek",
            parameters=[
                ToolParam("subject", "string", "调查对象（实体名）", required=False, default=""),
                ToolParam("hints", "string", "用户关注方向", required=False, default=""),
                ToolParam("goal", "string", "调查意图", required=False, default=""),
            ],
        ),
        filter_handler,
    )

    # 信息整合推理（报告生成）
    reg.register(
        ToolSpec(
            name="synthesize_report",
            description="信息整合推理：把筛选结果整合为逻辑链路/发现/时间线/待验证的报告。",
            category="synthesize", cost="high", provider="deepseek",
            parameters=[
                ToolParam("entity", "string", "调查实体名"),
                ToolParam("hints", "string", "用户关注方向", required=False, default=""),
                ToolParam("goal", "string", "调查意图", required=False, default=""),
            ],
        ),
        lambda entity, hints="", goal="", report=None: _synthesize_handler(entity, hints=hints, goal=goal, report=report),
    )

    # 知识图谱
    reg.register(
        ToolSpec(
            name="graph_merge",
            description="知识图谱写入：实体/关系 merge 进 Neo4j。",
            category="graph", cost="medium", provider="neo4j",
            parameters=[
                ToolParam("entity", "string", "实体名"),
                ToolParam("relation", "string", "关系类型", required=False, default=""),
                ToolParam("target", "string", "关联实体", required=False, default=""),
            ],
        ),
        lambda entity, relation="", target="": _graph_merge_handler(entity, relation, target),
    )


def _synthesize_handler(entity: str, hints: str = "", goal: str = "", report=None):
    """独立报告生成（不依赖整个调查图）。report 为空时用空报告（仅实体名）"""
    from entity_intel.searcher import EntitySearcher
    from shared.models.entity_report import EntityReport
    if report is None:
        report = EntityReport(entity_name=entity)
    return EntitySearcher().synthesize(report, hints=hints, goal=goal)


def _graph_merge_handler(entity: str, relation: str = "", target: str = ""):
    from shared.storage.neo4j_client import Neo4jClient
    client = Neo4jClient()
    client.merge_entity(entity)
    if relation and target:
        client.merge_relation(entity, target, relation)
    return {"entity": entity, "merged": True, "relation": relation or None, "target": target or None}

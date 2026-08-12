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
    """工具调用留痕（P1.3 审计基础）"""
    tool: str
    params: dict
    ts: float = field(default_factory=time.time)
    duration_s: float = 0.0
    status: str = "ok"           # ok / error
    summary: str = ""            # 结果摘要（不存全量，避免日志膨胀）
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "tool": self.tool,
            "params": {k: (v if not isinstance(v, (dict, list)) else "<obj>") for k, v in self.params.items()},
            "ts": self.ts,
            "duration_s": round(self.duration_s, 2),
            "status": self.status,
            "summary": self.summary[:500],
            "error": self.error[:300],
        }


class ToolRegistry:
    """工具注册表 — 注册/查询/调用/审计"""

    def __init__(self):
        self._tools: dict[str, ToolSpec] = {}
        self._handlers: dict[str, Callable] = {}
        self._audit: list[ToolCallRecord] = []

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

    def call(self, name: str, **params) -> Any:
        """调用工具：执行 handler + 记录审计。抛 ToolNotFoundError / 透传 handler 异常"""
        if name not in self._handlers:
            raise ToolNotFoundError(f"工具未注册: {name}")

        handler = self._handlers[name]
        spec = self._tools[name]
        record = ToolCallRecord(tool=name, params=params)
        t0 = time.time()
        logger = get_current_logger()

        try:
            result = handler(**params)
            record.duration_s = time.time() - t0
            record.summary = self._summarize(result)
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

    # ── 审计 ───────────────────────────────────────────

    def audit(self, limit: int = 50) -> list[dict]:
        """本次会话的工具调用记录（回放用）"""
        return [r.to_dict() for r in self._audit[-limit:]]

    def clear_audit(self) -> None:
        self._audit = []

    # ── 内部 ───────────────────────────────────────────

    def _summarize(self, result: Any) -> str:
        """结果摘要（避免审计日志存全量数据）"""
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


class ToolNotFoundError(Exception):
    pass


# ── 全局单例 + 构建器 ─────────────────────────────────

_default_registry: ToolRegistry | None = None


def get_registry() -> ToolRegistry:
    """获取全局 Tool Registry（惰性构建，避免导入时初始化外部依赖）"""
    global _default_registry
    if _default_registry is None:
        _default_registry = build_default_registry()
    return _default_registry


def build_default_registry() -> ToolRegistry:
    """构建默认工具集：7 个爬虫 Provider + 处理步骤"""
    reg = ToolRegistry()
    _register_crawlers(reg)
    _register_processors(reg)
    return reg


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
        lambda entity, hints="", goal="": _synthesize_handler(entity, hints=hints, goal=goal),
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


def _synthesize_handler(entity: str, hints: str = "", goal: str = ""):
    """独立报告生成（不依赖整个调查图）"""
    from entity_intel.searcher import EntitySearcher
    from shared.models.entity_report import EntityReport
    return EntitySearcher().synthesize(
        EntityReport(entity_name=entity), hints=hints, goal=goal
    )


def _graph_merge_handler(entity: str, relation: str = "", target: str = ""):
    from shared.storage.neo4j_client import Neo4jClient
    client = Neo4jClient()
    client.merge_entity(entity)
    if relation and target:
        client.merge_relation(entity, target, relation)
    return {"entity": entity, "merged": True, "relation": relation or None, "target": target or None}

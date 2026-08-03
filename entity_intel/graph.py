"""LangGraph 调查流程图 — 产品级调查编排

流水线: search → filter → extract → synthesize → review(interrupt) → graph_build
HITL:   review 节点 interrupt()，用户审阅报告后通过 Command(resume) 决定 approve/reject
Checkpoint: SQLite (data/checkpoints.db) — 支持断点续跑/审计

节点职责:
    search      多源并行搜索 → EntityReport
    filter      相关性筛选（通用 shared/nlp/relevance）
    extract     LLM 实体抽取 → related_entities
    synthesize  信息整合推理 → AnalysisReport（逻辑链路）
    review      interrupt() 暂停，等用户审阅报告（HITL）
    graph_build 用户批准后构建 Neo4j 知识图谱
"""

import sqlite3
import time
from pathlib import Path
from typing import Annotated, TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from shared.models.analysis_report import AnalysisReport
from shared.models.entity_report import EntityReport

_CHECKPOINT_DIR = Path(__file__).parent.parent / "data"
_CHECKPOINT_DB = _CHECKPOINT_DIR / "checkpoints.db"


# ── 图状态 ────────────────────────────────────────────

class InvestigationState(TypedDict):
    entity_name: str
    hints: str
    goal: str
    report: EntityReport
    analysis: AnalysisReport
    progress: list[dict]
    error: str
    decision: str          # approve / reject（review 节点产出）


# ── 节点实现 ──────────────────────────────────────────

def _add_progress(state: InvestigationState, phase: str, detail: str = "") -> list[dict]:
    """追加一条进度记录"""
    return state.get("progress", []) + [
        {"phase": phase, "detail": detail, "ts": time.time()}
    ]


def search_node(state: InvestigationState) -> dict:
    """多源并行搜索"""
    from entity_intel.searcher import EntitySearcher

    searcher = EntitySearcher()
    report = searcher.search(state["entity_name"], max_per_source=10)
    return {
        "report": report,
        "progress": _add_progress(
            state, "search_done", f"搜索完成: {report.total_results} 条"
        ),
    }


def filter_node(state: InvestigationState) -> dict:
    """相关性筛选"""
    from entity_intel.relevance import filter_entity_report

    report = state["report"]
    filter_entity_report(report, hints=state["hints"], goal=state["goal"])
    kept = report.metadata.get("filtered", {}).get("final_kept", 0)
    return {
        "report": report,
        "progress": _add_progress(state, "filter_done", f"筛选保留 {kept} 条"),
    }


def extract_node(state: InvestigationState) -> dict:
    """LLM 实体抽取"""
    from shared.llm.extractor import EntityExtractor

    report = state["report"]
    EntityExtractor().extract_from_report(report)
    return {
        "report": report,
        "progress": _add_progress(
            state, "extract_done", f"抽取 {len(report.related_entities)} 个相关实体"
        ),
    }


def synthesize_node(state: InvestigationState) -> dict:
    """信息整合推理 → 分析报告"""
    from entity_intel.synthesizer import Synthesizer

    report = state["report"]
    analysis = Synthesizer().synthesize(
        report, hints=state["hints"], goal=state["goal"]
    )
    return {
        "analysis": analysis,
        "progress": _add_progress(
            state, "synthesize_done",
            f"报告生成: {len(analysis.logical_chains)} 条逻辑链路",
        ),
    }


def review_node(state: InvestigationState) -> dict:
    """HITL: 暂停等用户审阅报告"""
    analysis = state["analysis"]
    decision = interrupt({
        "question": "是否基于此报告构建知识图谱？",
        "entity_name": state["entity_name"],
        "report": analysis.to_dict(),
        "report_markdown": analysis.to_markdown(),
    })
    action = "approve"
    if isinstance(decision, dict):
        action = decision.get("action", "reject")
    return {
        "decision": action,
        "progress": _add_progress(state, "review", f"用户决定: {action}"),
    }


def graph_build_node(state: InvestigationState) -> dict:
    """构建 Neo4j 知识图谱"""
    from shared.storage.neo4j_client import Neo4jClient

    analysis = state["analysis"]
    client = Neo4jClient()
    client.ensure_indexes()

    # 核心实体
    client.merge_entity(
        state["entity_name"],
        entity_type="person",
        summary=analysis.entity_summary[:500],
        source="investigation",
    )
    # 建议实体
    for e in analysis.suggested_entities:
        client.merge_entity(
            e.get("name", ""), entity_type=e.get("type", ""), source="investigation"
        )
    # 建议关系
    rel_count = 0
    for r in analysis.suggested_relations:
        frm, to, rel = r.get("from", ""), r.get("to", ""), r.get("relation", "")
        if frm and to and rel:
            client.merge_relation(frm, to, rel, source="investigation")
            rel_count += 1

    return {
        "progress": _add_progress(
            state, "graph_done",
            f"图谱构建完成: {len(analysis.suggested_entities)} 实体, {rel_count} 关系",
        ),
    }


def route_after_review(state: InvestigationState) -> str:
    return "graph_build" if state.get("decision") == "approve" else END


# ── 图构建 ────────────────────────────────────────────

_graph: dict = {}  # 缓存编译后的图（进程内单例）


def get_checkpointer():
    """创建 SQLite checkpointer（单例连接），注册自定义 dataclass 类型"""
    if "_checkpointer" not in _graph:
        _CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(_CHECKPOINT_DB), check_same_thread=False)

        # 注册自定义类型，避免 msgpack 反序列化警告
        from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
        from shared.crawlers.base import SearchResult
        from shared.models.analysis_report import (
            AnalysisReport, Finding, LogicalChain, LogicalLink, TimelineEvent,
        )
        from shared.models.entity_report import EntityReport

        serde = JsonPlusSerializer().with_msgpack_allowlist([
            SearchResult, EntityReport,
            AnalysisReport, Finding, LogicalChain, LogicalLink, TimelineEvent,
        ])

        _graph["_checkpointer"] = SqliteSaver(conn, serde=serde)
    return _graph["_checkpointer"]


def get_graph():
    """获取编译后的调查图（单例）"""
    if "graph" not in _graph:
        builder = StateGraph(InvestigationState)
        builder.add_node("search", search_node)
        builder.add_node("filter", filter_node)
        builder.add_node("extract", extract_node)
        builder.add_node("synthesize", synthesize_node)
        builder.add_node("review", review_node)
        builder.add_node("graph_build", graph_build_node)

        builder.add_edge(START, "search")
        builder.add_edge("search", "filter")
        builder.add_edge("filter", "extract")
        builder.add_edge("extract", "synthesize")
        builder.add_edge("synthesize", "review")
        builder.add_conditional_edges(
            "review", route_after_review,
            {"graph_build": "graph_build", END: END},
        )
        builder.add_edge("graph_build", END)

        _graph["graph"] = builder.compile(checkpointer=get_checkpointer())
    return _graph["graph"]


def run_investigation(entity_name: str, hints: str = "", goal: str = "") -> tuple[dict, str]:
    """
    执行调查到 review 暂停点。

    Returns:
        (result_state, thread_id) — result 含 analysis/report/progress；
        thread_id 用于后续 resume。
    """
    thread_id = f"inv_{int(time.time()*1000)}"
    config = {"configurable": {"thread_id": thread_id}}

    initial = {
        "entity_name": entity_name,
        "hints": hints,
        "goal": goal,
        "report": EntityReport(entity_name=entity_name),
        "analysis": AnalysisReport(entity_name=entity_name),
        "progress": [],
        "error": "",
        "decision": "",
    }

    result = get_graph().invoke(initial, config)
    return result, thread_id


def resume_investigation(thread_id: str, action: str) -> dict:
    """
    用户在 review 暂停点做出决定后继续执行。

    Args:
        thread_id: run_investigation 返回的 thread_id
        action: "approve" → 构建图谱; "reject" → 结束
    """
    config = {"configurable": {"thread_id": thread_id}}
    result = get_graph().invoke(Command(resume={"action": action}), config)
    return result


def get_state(thread_id: str) -> dict:
    """获取指定线程的当前状态（供审计/查询）"""
    config = {"configurable": {"thread_id": thread_id}}
    snapshot = get_graph().get_state(config)
    return snapshot.values


# ==================== 自检 ====================
if __name__ == "__main__":
    print("LangGraph 调查图自检")
    print("=" * 40)

    graph = get_graph()
    print(f"✅ 图已编译")

    # 检查节点
    nodes = list(graph.get_graph().nodes.keys())
    print(f"节点: {nodes}")

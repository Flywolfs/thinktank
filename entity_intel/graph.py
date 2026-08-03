"""LangGraph 调查流程图 — 产品级多轮深挖（Phase B v2）

流水线: search → filter → extract → deep_audio → analyze_leads →(继续,回到 search)
                                                          │(收敛/达轮数上限)
                                                          ▼
                                                      synthesize → review(interrupt) → graph_build
HITL:   review 节点 interrupt()，用户审阅报告后通过 Command(resume) 决定 approve/reject
Checkpoint: SQLite (data/checkpoints.db) — 支持断点续跑/审计

多轮深挖 (Phase B v2):
    analyze_leads 每轮生成"搜索计划"（3-5 个搜索角度/关键词），
    search 节点按计划多角度并行搜索，结果累积到 report（跨轮保留）。
    deep_audio 节点对高相关 B站视频做 ASR 转录，补充深度内容。
    线索队列维护发现的新实体；visited 防环路；all_rounds 记录每轮轨迹。
"""

import sqlite3
import time
from pathlib import Path
from typing import TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from shared.models.analysis_report import AnalysisReport
from shared.models.entity_report import EntityReport
from shared.models.leads import Lead, LeadQueue
from shared.llm.client import LLMClient

_CHECKPOINT_DIR = Path(__file__).parent.parent / "data"
_CHECKPOINT_DB = _CHECKPOINT_DIR / "checkpoints.db"

MAX_ASR_VIDEOS = 15  # 每次调查最多转录多少个视频（成本控制）


# ── 图状态 ────────────────────────────────────────────

class InvestigationState(TypedDict):
    entity_name: str
    hints: str
    goal: str
    max_rounds: int

    # 多轮深挖状态
    round: int                      # 当前轮次（从 1 开始）
    search_plan: list[str]          # 本轮搜索计划（多个搜索角度）
    visited: list[str]              # 已搜索过的关键词（防环路）
    leads: list[dict]               # 线索队列（Lead.to_dict()）
    all_rounds: list[dict]          # 每轮结果摘要
    asr_count: int                  # 已转录视频数（成本控制）

    # 当前轮结果（report 跨轮累积）
    report: EntityReport
    analysis: AnalysisReport
    progress: list[dict]
    error: str
    decision: str                   # continue/stop/approve/reject


# ── 工具 ──────────────────────────────────────────────

def _add_progress(state: InvestigationState, phase: str, detail: str = "") -> list[dict]:
    return state.get("progress", []) + [
        {"phase": phase, "detail": detail, "ts": time.time()}
    ]


def _merge_reports(target: EntityReport, new: EntityReport):
    """把一次搜索的结果合并进累积 report（按 URL 去重）"""
    seen = {r.url for r in target.web_results + target.social_results + target.knowledge_results}
    for r in new.web_results:
        if r.url not in seen:
            target.web_results.append(r)
            seen.add(r.url)
    for r in new.social_results:
        if r.url not in seen:
            target.social_results.append(r)
            seen.add(r.url)
    for r in new.knowledge_results:
        if r.url not in seen:
            target.knowledge_results.append(r)
            seen.add(r.url)


# ── 节点实现 ──────────────────────────────────────────

def search_node(state: InvestigationState) -> dict:
    """按搜索计划多角度并行搜索，结果累积到 report"""
    from entity_intel.searcher import EntitySearcher

    searcher = EntitySearcher()
    plan = state.get("search_plan") or [state["entity_name"]]
    report = state.get("report") or EntityReport(entity_name=state["entity_name"])

    visited = list(state.get("visited", []))
    details = []
    for q in plan:
        q = q.strip()
        if not q:
            continue
        try:
            r = searcher.search(q, max_per_source=10)
            _merge_reports(report, r)
            details.append(f"'{q}'+{r.total_results}")
            if q not in visited:
                visited.append(q)
        except Exception as e:
            details.append(f"'{q}'✗{str(e)[:40]}")

    return {
        "report": report,
        "visited": visited,
        "progress": _add_progress(
            state, "search_done",
            f"[第{state.get('round', 1)}轮] 计划{len(plan)}角度: {'; '.join(details)}",
        ),
    }


def filter_node(state: InvestigationState) -> dict:
    """相关性筛选（对累积的 report 整体筛）"""
    from entity_intel.relevance import filter_entity_report

    report = state["report"]
    filter_entity_report(report, hints=state["hints"], goal=state["goal"])
    kept = report.metadata.get("filtered", {}).get("final_kept", 0)
    return {
        "report": report,
        "progress": _add_progress(state, "filter_done", f"筛选后保留 {kept} 条"),
    }


def extract_node(state: InvestigationState) -> dict:
    """LLM 实体抽取（对累积结果）"""
    from shared.llm.extractor import EntityExtractor

    report = state["report"]
    EntityExtractor().extract_from_report(report)
    return {
        "report": report,
        "progress": _add_progress(
            state, "extract_done", f"抽取 {len(report.related_entities)} 个相关实体"
        ),
    }


def deep_audio_node(state: InvestigationState) -> dict:
    """对高相关 B站视频做 ASR 转录，补充深度内容（成本控制: 最多 MAX_ASR_VIDEOS 个）"""
    report = state["report"]
    asr_count = state.get("asr_count", 0)
    if asr_count >= MAX_ASR_VIDEOS:
        return {"report": report}

    # 找已转录过的 bvid
    transcribed = set()
    for r in report.social_results:
        if r.metadata.get("asr_done"):
            transcribed.add(r.metadata.get("bvid", ""))

    # 取未转录的 B站视频（core_owner 优先），每轮最多 4 个
    candidates = [
        r for r in report.social_results
        if r.source == "bilibili"
        and r.metadata.get("bvid")
        and r.metadata.get("bvid") not in transcribed
    ]
    if not candidates:
        return {"report": report, "asr_count": asr_count}

    candidates.sort(key=lambda r: (
        0 if r.author == state["entity_name"] else 1,  # core_owner 优先
        -r.metadata.get("play", 0),                     # 播放量高优先
    ))
    todo = candidates[: min(4, MAX_ASR_VIDEOS - asr_count)]

    from shared.crawlers.bilibili import BilibiliCLIProvider
    from shared.utils import config
    import httpx

    provider = BilibiliCLIProvider()
    done = 0
    for v in todo:
        bvid = v.metadata["bvid"]
        try:
            segs = provider.download_audio_asr(bvid)
            merged = provider.merge_segments(segs, target_seconds=240)
            texts = []
            for m in merged:
                with open(m, "rb") as f:
                    resp = httpx.post(
                        f"{config.QWEN3_ASR_URL}/v1/audio/transcriptions",
                        files={"file": ("audio.wav", f, "audio/wav")},
                        data={"language": "zh"}, timeout=300,
                    )
                resp.raise_for_status()
                texts.append(resp.json().get("text", ""))
            full_text = "\n".join(t for t in texts if t)
            # 全文存 metadata，不污染 content（避免被摘要截断）
            v.metadata["asr_text"] = full_text
            v.metadata["asr_done"] = True
            done += 1
        except Exception as e:
            v.metadata["asr_error"] = str(e)[:100]
            continue

    new_count = asr_count + done
    return {
        "report": report,
        "asr_count": new_count,
        "progress": _add_progress(
            state, "deep_audio", f"B站视频 ASR 转录 {done} 个 (累计 {new_count}/{MAX_ASR_VIDEOS})"
        ),
    }


def analyze_leads_node(state: InvestigationState) -> dict:
    """核心：LLM 生成下一轮搜索计划（多角度），决定继续还是收敛"""
    llm = LLMClient()
    report = state["report"]

    # 维护线索队列：把抽取的实体加入（去重、防环路）
    leads = [Lead(**l) for l in state.get("leads", [])]
    queue = LeadQueue()
    for l in leads:
        queue.add(l)
    for e in report.related_entities:
        name = e.get("name", "")
        if not name or name == state["entity_name"]:
            continue
        if name in state.get("visited", []) or name in [l.name for l in queue.all()]:
            continue
        rel = e.get("relation", "")
        queue.add(Lead(
            name=name,
            relation=rel,
            priority="high" if rel in ("创始人", "CEO", "控制", "控股", "投资", "资助") else "medium",
            reason=f"实体抽取: {rel or '关联'}",
            source_round=state.get("round", 1),
        ))

    round_num = state.get("round", 1)
    max_rounds = state.get("max_rounds", 30)

    # LLM 生成搜索计划
    plan, should_continue, plan_reason = _generate_search_plan(llm, state, report, queue)

    # 记录本轮
    all_rounds = list(state.get("all_rounds", []))
    all_rounds.append({
        "round": round_num,
        "plan": plan,
        "total_leads": len(queue.all()),
        "findings": [f.get("name", "") for f in report.related_entities[-10:]],
        "decide_continue": should_continue,
        "reason": plan_reason,
    })

    can_continue = should_continue and bool(plan) and round_num < max_rounds

    result: dict = {
        "leads": queue.to_dict_list(),
        "all_rounds": all_rounds,
        "progress": _add_progress(
            state, "analyze_leads",
            f"计划{len(plan)}角度: {'; '.join(plan[:3])}{'...' if len(plan)>3 else ''}"
            if can_continue else f"收敛({plan_reason})",
        ),
    }

    if can_continue:
        result["search_plan"] = plan
        result["round"] = round_num + 1
        result["decision"] = "continue"
    else:
        result["decision"] = "stop"

    return result


def _generate_search_plan(
    llm: LLMClient, state: InvestigationState, report: EntityReport, queue: LeadQueue
) -> tuple[list[str], bool, str]:
    """LLM 生成下一轮搜索计划（3-5 个搜索角度），判断是否值得继续"""
    try:
        leads_desc = "\n".join(
            f"- {l.name} ({l.relation}, {l.priority}) {'已追' if l.searched else '待追'}"
            for l in queue.all()
        ) or "（空）"
        findings_desc = "\n".join(
            f"- {e.get('name', '')} --{e.get('relation', '')}--> {report.entity_name}"
            for e in report.related_entities[:15]
        ) or "（无）"
        visited_desc = ", ".join(state.get("visited", [])) or "（无）"
        already = "\n".join(
            f"- {f.title}" for f in report.web_results[-8:] + report.social_results[-8:]
        )[:1200] or "（无）"

        prompt = f"""
调查核心实体: {state["entity_name"]}
用户关注: {state.get("hints", "") or "（无）"}
调查意图: {state.get("goal", "") or "全面调查"}
当前轮次: {state.get("round", 1)} / {state.get("max_rounds", 30)}

【已搜索过的关键词】
{visited_desc}

【本轮已发现的实体关系】
{findings_desc}

【线索队列（待追实体）】
{leads_desc}

【本轮已收集的部分结果】
{already}

请为【下一轮】生成搜索计划。输出 JSON:
{{
  "continue": true/false,
  "plan": ["搜索词1", "搜索词2", "搜索词3"],
  "reason": "为什么继续/停止"
}}

搜索计划生成规则（结合情报分析方法论）:
- 生成 3-5 个【不同角度】的搜索词，不要全是"实体名+线索名"的简单拼接
- 覆盖多个维度，例如:
  * 历史回溯: "雷军 2010 创办小米" "雷军 早年 金山"
  * 利益链条: "雷军 顺为资本 投资" "雷军 持股"
  * 争议/风险: "雷军 争议" "小米 诉讼"
  * 人物关系: "雷军 与 柳传志" "雷军 同学 校友"
  * 时间线事件: "雷军 上市" "雷军 年度演讲"
  * 用户关注方向(hints)优先
- 每个搜索词必须是【尚未搜索过】的新角度（对比 visited），避免环路
- 如果已多轮且信息增量明显下降，或线索/角度已穷尽 → continue=false
- continue=true 时 plan 至少 3 个搜索词
"""
        data = llm.extract_json(prompt, "请生成搜索计划", temperature=0.3, max_tokens=2000)
        plan = [p.strip() for p in data.get("plan", []) if p and p.strip()]
        plan = [p for p in plan if p not in state.get("visited", [])][:5]

        if data.get("continue") and plan:
            return plan, True, data.get("reason", "")
        return [], False, data.get("reason", "LLM 判定信息已收敛")
    except Exception:
        # LLM 失败：若有 high 线索，保守追一个
        pending = queue.next()
        if pending and pending.priority == "high":
            q = f"{state['entity_name']} {pending.name}"
            return [q], True, "LLM 决策失败，保守追 high 线索"
        return [], False, "LLM 决策失败，停止"


def synthesize_node(state: InvestigationState) -> dict:
    """信息整合推理 → 最终分析报告（基于累积结果）"""
    from entity_intel.synthesizer import Synthesizer

    report = state["report"]
    analysis = Synthesizer().synthesize(
        report, hints=state["hints"], goal=state["goal"]
    )
    return {
        "analysis": analysis,
        "progress": _add_progress(
            state, "synthesize_done",
            f"报告生成: {len(analysis.logical_chains)} 条逻辑链路, "
            f"{len(analysis.key_findings)} 条发现",
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
        "investigation_rounds": state.get("all_rounds", []),
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

    client.merge_entity(
        state["entity_name"],
        entity_type="person",
        summary=analysis.entity_summary[:500],
        source="investigation",
    )
    for e in analysis.suggested_entities:
        client.merge_entity(
            e.get("name", ""), entity_type=e.get("type", ""), source="investigation"
        )
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


# ── 路由 ──────────────────────────────────────────────

def route_after_analyze(state: InvestigationState) -> str:
    return "search" if state.get("decision") == "continue" else "synthesize"


def route_after_review(state: InvestigationState) -> str:
    return "graph_build" if state.get("decision") == "approve" else END


# ── 图构建 ────────────────────────────────────────────

_graph: dict = {}


def get_checkpointer():
    if "_checkpointer" not in _graph:
        _CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(_CHECKPOINT_DB), check_same_thread=False)

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
    if "graph" not in _graph:
        builder = StateGraph(InvestigationState)
        builder.add_node("search", search_node)
        builder.add_node("filter", filter_node)
        builder.add_node("extract", extract_node)
        builder.add_node("deep_audio", deep_audio_node)
        builder.add_node("analyze_leads", analyze_leads_node)
        builder.add_node("synthesize", synthesize_node)
        builder.add_node("review", review_node)
        builder.add_node("graph_build", graph_build_node)

        builder.add_edge(START, "search")
        builder.add_edge("search", "filter")
        builder.add_edge("filter", "extract")
        builder.add_edge("extract", "deep_audio")
        builder.add_edge("deep_audio", "analyze_leads")
        builder.add_conditional_edges(
            "analyze_leads", route_after_analyze,
            {"search": "search", "synthesize": "synthesize"},
        )
        builder.add_edge("synthesize", "review")
        builder.add_conditional_edges(
            "review", route_after_review,
            {"graph_build": "graph_build", END: END},
        )
        builder.add_edge("graph_build", END)

        _graph["graph"] = builder.compile(checkpointer=get_checkpointer())
    return _graph["graph"]


# ── 对外接口 ──────────────────────────────────────────

def run_investigation(
    entity_name: str, hints: str = "", goal: str = "", max_rounds: int = 30
) -> tuple[dict, str]:
    """执行调查（多轮深挖）到 review 暂停点。返回 (result_state, thread_id)"""
    thread_id = f"inv_{int(time.time()*1000)}"
    config = {"configurable": {"thread_id": thread_id}}

    initial: InvestigationState = {
        "entity_name": entity_name,
        "hints": hints,
        "goal": goal,
        "max_rounds": max_rounds,
        "round": 1,
        "search_plan": [entity_name],
        "visited": [],
        "leads": [],
        "all_rounds": [],
        "asr_count": 0,
        "report": EntityReport(entity_name=entity_name),
        "analysis": AnalysisReport(entity_name=entity_name),
        "progress": [],
        "error": "",
        "decision": "",
    }

    result = get_graph().invoke(initial, config)
    return result, thread_id


def resume_investigation(thread_id: str, action: str) -> dict:
    """用户在 review 暂停点做出决定后继续。action: approve / reject"""
    config = {"configurable": {"thread_id": thread_id}}
    result = get_graph().invoke(Command(resume={"action": action}), config)
    return result


def get_state(thread_id: str) -> dict:
    config = {"configurable": {"thread_id": thread_id}}
    snapshot = get_graph().get_state(config)
    return snapshot.values


# ==================== 自检 ====================
if __name__ == "__main__":
    print("LangGraph 多轮深挖图 (Phase B v2) 自检")
    print("=" * 40)
    graph = get_graph()
    print(f"✅ 图已编译")
    print(f"节点: {list(graph.get_graph().nodes.keys())}")

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
from langgraph.types import Command, Send, interrupt

from typing import Annotated
from operator import add

from shared.models.analysis_report import AnalysisReport
from shared.models.entity_report import EntityReport
from shared.models.leads import Lead, LeadQueue
from shared.llm.client import LLMClient
from shared.utils import config

_CHECKPOINT_DIR = Path(__file__).parent.parent / "data"
_CHECKPOINT_DB = _CHECKPOINT_DIR / "checkpoints.db"

MAX_ASR_VIDEOS = 15  # 每次调查最多转录多少个视频（成本控制）


# ── 图状态 ────────────────────────────────────────────

class InvestigationState(TypedDict):
    entity_name: str
    hints: str
    goal: str
    max_rounds: int
    plan_provider: str              # auto/hermes/local（覆盖 config.PLAN_PROVIDER）

    # Plan-and-Execute 状态
    plan: list[dict]                # 维度模板 [{name, methodology_source, rationale, queries, priority}]
    plan_index: int                 # 当前执行到第几个维度（0-based）

    # 多轮深挖状态
    round: int                      # 当前轮次（从 1 开始）
    search_plan: list[str]          # 本轮搜索计划（当前维度的多个搜索角度）
    visited: list[str]              # 已搜索过的关键词（防环路）
    leads: list[dict]               # 线索队列（Lead.to_dict()）
    all_rounds: list[dict]          # 每轮结果摘要
    asr_count: int                  # 已转录视频数（成本控制）

    # P2.2 线索并行状态
    parallel_leads: Annotated[list[dict], add]  # 本轮并行深挖的线索（Send 分发）
    parallel_results: Annotated[list[dict], add]  # 各线索搜索结果（合并用）

    # P2.3 中途调整方向
    adjustable: bool                # true=每轮 analyze_leads interrupt 等用户决定

    # P4.2 图谱递归血统
    parent_job_id: str              # 父调查 job_id（图谱深挖来源）
    parent_entity: str              # 父调查的核心实体名（血统边起点）
    parent_relation: str            # 与父实体的关系

    # 当前轮结果（report 跨轮累积）
    report: EntityReport
    analysis: AnalysisReport
    progress: list[dict]
    error: str
    decision: str                   # continue/stop/approve/reject
    job_id: str                     # 日志用 job_id


# ── 工具 ──────────────────────────────────────────────

def _add_progress(state: InvestigationState, phase: str, detail: str = "") -> list[dict]:
    return state.get("progress", []) + [
        {"phase": phase, "detail": detail, "ts": time.time()}
    ]


def _get_logger(state: InvestigationState):
    """从 state 获取调查日志器"""
    from shared.utils.logger import InvestigationLogger
    return InvestigationLogger(
        job_id=state.get("job_id", "unknown"),
        thread_id="",
    )


def _load_methodology_snippet() -> str:
    """加载方法论文档的 Plan 章节作为 plan_node 的 system 上下文"""
    from pathlib import Path
    p = Path(__file__).parent / "INVESTIGATION_METHODOLOGY.md"
    try:
        text = p.read_text(encoding="utf-8")
        # 提取"调查计划制定"章节
        if "## 调查计划制定" in text:
            start = text.index("## 调查计划制定")
            end = text.index("## 调查流程", start) if "## 调查流程" in text[start:] else len(text)
            return text[start:end]
        return text[:6000]
    except Exception:
        return ""


def _local_plan_generator(entity: str, hints: str = "", goal: str = "") -> dict:
    """Local 路线（路线A）: 现有 5 步多轮推理生成 plan。

    供 PlanProvider 降级调用。输出统一 {dimensions, max_rounds, plan_summary}。
    """
    from shared.llm.client import LLMClient

    llm = LLMClient()
    methodology = _load_methodology_snippet()

    entity_type, type_reason = _plan_analyze_type(llm, entity, hints, goal)
    candidates, cand_summary = _plan_generate_candidates(
        llm, entity, hints, goal, entity_type, methodology
    )
    ranked = _plan_rank_dimensions(llm, entity, hints, goal, entity_type, candidates)
    issues = _plan_self_check(llm, entity, hints, goal, ranked, methodology)
    final_dims, max_rounds, summary = _plan_fix_and_output(
        llm, entity, hints, goal, ranked, issues, max_rounds_cap=30
    )
    return {
        "dimensions": final_dims,
        "max_rounds": max_rounds,
        "plan_summary": summary,
    }


def plan_node(state: InvestigationState) -> dict:
    """Plan 阶段（P0.3 融合版）：PlanProvider 策略选择。

    模式（PLAN_PROVIDER env）:
      auto  (默认) → Hermes 优先，失败/校验不过 → 降级 Local 5步推理
      hermes       → 强制 Docker Hermes，失败报错
      local        → 只用自研 5 步推理（离线/省成本）

    所有路线输出都经 PlanValidator 统一校验（结构/方法论合法性/基线第一/query 具体性）。
    """
    logger = _get_logger(state)
    logger.node_start("plan", {"entity": state["entity_name"], "hints": state.get("hints", "")})
    t0 = time.time()
    entity = state["entity_name"]
    hints = state.get("hints", "")
    goal = state.get("goal", "")

    from shared.plan.provider import PlanGenerationError, PlanProvider
    from shared.plan.validator import PlanValidator

    provider = PlanProvider(
        mode=state.get("plan_provider") or config.PLAN_PROVIDER,
        validator=PlanValidator(),
        local_generator=_local_plan_generator,
    )

    try:
        plan = provider.generate(entity, hints, goal)
    except PlanGenerationError as e:
        logger.error("plan", f"plan 生成失败: {e}")
        # 兜底：单维度最小计划，保证流程可继续
        fallback = {
            "dimensions": [{
                "name": "基线档案",
                "methodology_source": "核心方法论§1 穷尽明面信息",
                "rationale": f"兜底计划：建立 {entity} 基准",
                "queries": [f"{entity} 维基百科", f"{entity} 百度百科"],
                "priority": "high",
            }],
            "max_rounds": 2,
            "plan_summary": f"兜底计划（{e}）",
            "provider": "fallback",
        }
        plan = fallback

    final_dims = plan["dimensions"]
    max_rounds = int(plan.get("max_rounds") or len(final_dims) + 1)
    max_rounds = max(1, min(max_rounds, state.get("max_rounds", 30)))
    summary = plan.get("plan_summary", "")
    provider_used = plan.get("provider", "?")

    logger.decision(
        "plan", "制定完成",
        reason=f"{len(final_dims)} 维度 / {max_rounds} 轮 / provider={provider_used}",
        detail={
            "dimensions": [d.get("name") for d in final_dims],
            "summary": summary,
            "provider": provider_used,
            "elapsed_s": round(time.time() - t0, 1),
        },
    )
    logger.node_end("plan", {
        "dimensions": len(final_dims),
        "max_rounds": max_rounds,
        "provider": provider_used,
    }, duration_ms=(time.time() - t0) * 1000)

    return {
        "plan": final_dims,
        "plan_index": 0,
        "max_rounds": max_rounds,
        "search_plan": final_dims[0]["queries"],
        "round": 1,
        "progress": _add_progress(
            state, "plan",
            f"制定调查计划: {len(final_dims)} 维度 / {max_rounds} 轮 "
            f"(provider={provider_used})\n"
            f"{summary or '; '.join(d['name'] for d in final_dims)}",
        ),
    }


# ── Plan 多步推理的步骤函数 ────────────────────────────

def _plan_analyze_type(llm, entity: str, hints: str, goal: str) -> tuple[str, str]:
    """Step 1: 分析实体类型"""
    prompt = f"""
调查核心实体: {entity}
用户关注方向: {hints or "（无）"}
调查意图: {goal or "全面调查"}

请判断该实体的【类型】。输出 JSON:
{{
  "type": "person_entrepreneur|person_public|organization|event|location|product|other",
  "reason": "判断理由（结合实体名特征）",
  "key_attributes": ["可能的关键属性，如：企业家/作家/明星等"]
}}

判断规则:
- person_entrepreneur: 企业家/创业者/投资人（如雷军、马云）
- person_public: 公众人物/作家/明星/学者（如蒋方舟、韩红）
- organization: 公司/机构/组织（如小米、字节跳动）
- event: 事件/争议/历史事件
- location: 地点/地区
- product: 产品/品牌
- other: 其他或不确定
"""
    try:
        data = llm.extract_json(prompt, "请分析实体类型", temperature=0.1, max_tokens=1000)
        return data.get("type", "other"), data.get("reason", "")
    except Exception:
        return "other", "类型分析失败，按通用处理"


def _plan_generate_candidates(llm, entity: str, hints: str, goal: str,
                              entity_type: str, methodology: str) -> tuple[list[dict], str]:
    """Step 2: 基于实体类型+方法论映射表生成候选维度"""
    prompt = f"""
调查核心实体: {entity}
已判定类型: {entity_type}
用户关注方向: {hints or "（无）"}
调查意图: {goal or "全面调查"}

【调查计划制定方法论（含映射表）】
{methodology[:6000]}

请基于【实体类型】和【方法论映射表】，生成候选调查维度（可多于最终数量，供下一步筛选）。
输出 JSON:
{{
  "dimensions": [
    {{
      "name": "维度名",
      "methodology_source": "引用的方法论来源（必须来自映射表）",
      "rationale": "为什么选这个维度（结合该实体的具体特征）",
      "queries": ["搜索角度1", "搜索角度2"],
      "priority": "high|medium|low"
    }}
  ],
  "summary": "一句话说明候选维度覆盖思路"
}}

规则:
1. 每个维度必须能在方法论映射表中找到来源（核心方法论§1-6 / 个人深扒§1-7 / Phase 2-9 / 元数据挖掘）
2. 生成 5-10 个候选维度（宁多勿少，下一步筛选）
3. 结合实体类型：企业家侧重资金链/关系网，公众人物侧重言论史/媒体反应，公司侧重工商/利益链条，事件侧重时间线/媒体
4. 每个维度 2-4 个具体搜索角度（结合实体名，不要泛泛的"搜索 实体名"）
"""
    try:
        data = llm.extract_json(prompt, "请生成候选维度", temperature=0.3, max_tokens=4000)
        dims = [d for d in data.get("dimensions", []) if d.get("name") and d.get("queries")]
        return dims, data.get("summary", "")
    except Exception:
        return [], "候选维度生成失败"


def _plan_rank_dimensions(llm, entity: str, hints: str, goal: str,
                          entity_type: str, candidates: list[dict]) -> list[dict]:
    """Step 3: 筛选排序（hints 优先、基线第一、去重叠）"""
    if not candidates:
        return [{
            "name": "基线档案",
            "methodology_source": "核心方法论§1 穷尽明面信息",
            "rationale": "兜底：先建立实体基准",
            "queries": [entity],
            "priority": "high",
        }]

    cand_desc = "\n".join(
        f"- {d.get('name')} [{d.get('priority', 'medium')}] 来源:{d.get('methodology_source', '?')} "
        f"queries:{d.get('queries', [])}"
        for d in candidates
    )
    prompt = f"""
调查核心实体: {entity}
实体类型: {entity_type}
用户关注方向: {hints or "（无）"}
调查意图: {goal or "全面调查"}

【候选维度】
{cand_desc}

请筛选并排序，输出最终维度列表（3-7 个）。输出 JSON:
{{
  "dimensions": [
    {{
      "name": "维度名",
      "methodology_source": "引用的方法论来源",
      "rationale": "为什么保留（结合实体+hints）",
      "queries": ["搜索角度1", "搜索角度2", "搜索角度3"],
      "priority": "high|medium|low"
    }}
  ],
  "max_rounds": 6,
  "summary": "一句话概述调查路径"
}}

规则:
1. 基线档案（核心方法论§1）永远第一
2. hints 明确提到的方向 → 提到最前 + high 优先级
3. 去掉重叠维度（覆盖同一信息层面的只留一个）
4. 保留 3-7 个，每个 2-4 个搜索角度
5. 搜索角度要具体（结合实体名），不要泛泛
"""
    try:
        data = llm.extract_json(prompt, "请筛选排序维度", temperature=0.2, max_tokens=4000)
        dims = [d for d in data.get("dimensions", []) if d.get("name") and d.get("queries")]
        return dims if dims else candidates[:5]
    except Exception:
        return candidates[:5]


def _plan_self_check(llm, entity: str, hints: str, goal: str,
                     dimensions: list[dict], methodology: str) -> list[str]:
    """Step 4: 自检合理性（覆盖度/方法论引用/重叠）"""
    if not dimensions:
        return ["无维度"]

    dim_desc = "\n".join(
        f"- {d.get('name')} 来源:{d.get('methodology_source', '?')} priority:{d.get('priority', '?')}"
        for d in dimensions
    )
    prompt = f"""
调查核心实体: {entity}
用户关注方向: {hints or "（无）"}

【已制定的维度】
{dim_desc}

请检查这个调查计划是否存在问题。输出 JSON:
{{
  "issues": [
    {{"issue": "问题描述", "severity": "high|medium|low", "suggestion": "修正建议"}}
  ]
}}

检查维度:
1. 覆盖度: 是否遗漏了该实体类型关键的信息层面？（企业家漏资金链？公众人物漏言论史？）
2. 方法论引用: 每个维度是否真的能在方法论映射表找到来源？
3. 重叠: 是否有两个维度在挖同一类信息？
4. hints 响应: 用户关注方向是否被充分覆盖？
5. 具体性: 搜索角度是否太泛（如只有"搜实体名"）？
"""
    try:
        data = llm.extract_json(prompt, "请自检调查计划", temperature=0.1, max_tokens=2000)
        issues = [
            f"[{i.get('severity', 'low')}] {i.get('issue', '')} → {i.get('suggestion', '')}"
            for i in data.get("issues", [])
            if i.get("issue")
        ]
        return issues
    except Exception:
        return []


def _plan_fix_and_output(llm, entity: str, hints: str, goal: str,
                         dimensions: list[dict], issues: list[str],
                         max_rounds_cap: int = 30) -> tuple[list[dict], int, str]:
    """Step 5: 根据自检问题修正，输出最终模板"""
    # 无自检问题 → 直接用
    if not issues:
        max_rounds = max(1, min(len(dimensions) + 1, max_rounds_cap))
        return dimensions, max_rounds, f"自检通过: {len(dimensions)} 维度"

    # 有自检问题 → 让 LLM 修正
    dim_desc = "\n".join(
        f"- {d.get('name')} 来源:{d.get('methodology_source', '?')} queries:{d.get('queries', [])}"
        for d in dimensions
    )
    issues_desc = "\n".join(f"- {i}" for i in issues)
    prompt = f"""
调查核心实体: {entity}
用户关注方向: {hints or "（无）"}

【当前维度】
{dim_desc}

【自检发现的问题】
{issues_desc}

请根据问题修正维度模板。输出修正后的 JSON:
{{
  "dimensions": [
    {{
      "name": "维度名",
      "methodology_source": "引用的方法论来源",
      "rationale": "为什么保留",
      "queries": ["搜索角度1", "搜索角度2", "搜索角度3"],
      "priority": "high|medium|low"
    }}
  ],
  "max_rounds": 6,
  "summary": "修正后的一句话概述"
}}

规则: 修正时优先解决 high severity 问题；保持 3-7 个维度；基线档案永远第一。
"""
    try:
        data = llm.extract_json(prompt, "请修正调查计划", temperature=0.2, max_tokens=4000)
        dims = [d for d in data.get("dimensions", []) if d.get("name") and d.get("queries")]
        if dims:
            max_rounds = int(data.get("max_rounds", len(dims) + 1))
            max_rounds = max(1, min(max_rounds, max_rounds_cap))
            return dims, max_rounds, data.get("summary", "已按自检问题修正")
        return dimensions, max(1, min(len(dimensions) + 1, max_rounds_cap)), "修正失败，保留原计划"
    except Exception:
        return dimensions, max(1, min(len(dimensions) + 1, max_rounds_cap)), "修正失败，保留原计划"


def _merge_reports(target: EntityReport, new: EntityReport):
    """把一次搜索的结果合并进累积 report（按 URL 去重）"""
    seen = {r.url for r in target.web_results + target.social_results + target.knowledge_results}
    added = 0
    for r in new.web_results:
        if r.url not in seen:
            target.web_results.append(r)
            seen.add(r.url)
            added += 1
    for r in new.social_results:
        if r.url not in seen:
            target.social_results.append(r)
            seen.add(r.url)
            added += 1
    for r in new.knowledge_results:
        if r.url not in seen:
            target.knowledge_results.append(r)
            seen.add(r.url)
            added += 1
    # 修复: 同步更新 total_results（之前从不更新 → 下游认为无数据）
    target.total_results = (
        len(target.web_results) + len(target.social_results) + len(target.knowledge_results)
    )
    return added


# ── 节点实现 ──────────────────────────────────────────

def search_node(state: InvestigationState) -> dict:
    """按搜索计划多角度搜索，结果累积到 report（走 Tool Registry，留痕）。
    性能策略: 第1个 query 用全源（含 MediaCrawler），
    其余 query 用快速源（跳过 MediaCrawler，避免重复爬取和输出目录冲突）。"""
    import concurrent.futures
    from shared.tools.registry import get_registry

    logger = _get_logger(state)
    t0 = time.time()
    reg = get_registry()
    job_id = state.get("job_id", "")
    plan = state.get("search_plan") or [state["entity_name"]]
    report = state.get("report") or EntityReport(entity_name=state["entity_name"])

    visited = list(state.get("visited", []))
    queries = [q.strip() for q in plan if q and q.strip()]
    logger.node_start("search", {"queries": queries, "round": state.get("round", 1)})

    details = []

    # 第1个 query：全源（含 MediaCrawler 知乎/小红书/微博）
    if queries:
        q0 = queries[0]
        try:
            r = reg.call("search_all", job_id=job_id, query=q0, max_per_source=10)
            _merge_reports(report, r)
            details.append(f"'{q0}'+{r.total_results}")
            if q0 not in visited:
                visited.append(q0)
        except Exception as e:
            details.append(f"'{q0}'✗{str(e)[:40]}")

    # 其余 query：快速源并行（无 MediaCrawler）
    rest = queries[1:]
    if rest:
        def _fast_search(q: str) -> tuple[str, int, str]:
            try:
                r = reg.call("search_all_fast", job_id=job_id, query=q, max_per_source=10)
                return q, r.total_results, ""
            except Exception as e:
                return q, 0, str(e)[:40]

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
            futures = {ex.submit(_fast_search, q): q for q in rest}
            for fut in concurrent.futures.as_completed(futures):
                q, cnt, err = fut.result()
                if err:
                    details.append(f"'{q}'✗{err}")
                else:
                    try:
                        r = reg.call("search_all_fast", job_id=job_id, query=q, max_per_source=10)
                        _merge_reports(report, r)
                        details.append(f"'{q}'+{r.total_results}")
                        if q not in visited:
                            visited.append(q)
                    except Exception as e:
                        details.append(f"'{q}'✗{str(e)[:40]}")

    logger.node_end("search", {"queries": len(queries), "results": len(details)},
                    duration_ms=(time.time() - t0) * 1000)
    return {
        "report": report,
        "visited": visited,
        "progress": _add_progress(
            state, "search_done",
            f"[第{state.get('round', 1)}轮] 计划{len(plan)}角度: {'; '.join(details)}",
        ),
    }


def filter_node(state: InvestigationState) -> dict:
    """相关性筛选（走 Tool Registry，留痕）"""
    from shared.tools.registry import get_registry

    report = state["report"]
    reg = get_registry()
    reg.call(
        "filter_report", job_id=state.get("job_id", ""),
        report=report, hints=state["hints"], goal=state["goal"],
    )
    kept = report.metadata.get("filtered", {}).get("final_kept", 0)
    return {
        "report": report,
        "progress": _add_progress(state, "filter_done", f"筛选后保留 {kept} 条"),
    }


def extract_node(state: InvestigationState) -> dict:
    """LLM 实体抽取（走 Tool Registry，留痕）"""
    from shared.tools.registry import get_registry

    report = state["report"]
    get_registry().call("extract_report", job_id=state.get("job_id", ""), report=report)
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
    """执行控制：维护线索队列 + 判断是否并行深挖线索 / 进入下一维度 / 完成

    adjustable=true 时每轮 interrupt，用户可选继续/调整方向（P2.3）"""
    logger = _get_logger(state)
    report = state["report"]
    plan = state.get("plan", [])
    plan_index = state.get("plan_index", 0)
    round_num = state.get("round", 1)
    max_rounds = state.get("max_rounds", 30)

    # P2.3: 可调整模式 → 每轮 interrupt 等用户决定方向
    if state.get("adjustable"):
        current_dim = plan[plan_index] if plan and plan_index < len(plan) else None
        remaining = [d.get("name", "?") for d in plan[plan_index:]] if plan else []
        decision = interrupt({
            "question": "调查进行中，是否调整方向？",
            "entity_name": state["entity_name"],
            "round": round_num,
            "current_dimension": (current_dim.get("name", "") if current_dim else ""),
            "remaining_dimensions": remaining,
            "leads_found": [l.get("name") for l in state.get("leads", [])[:10]],
            "options": {
                "continue": "继续当前计划",
                "adjust": "输入指令调整剩余维度（如：别追争议了，专注资金链）",
            },
        })
        action = "continue"
        instruction = ""
        if isinstance(decision, dict):
            action = decision.get("action", "continue")
            instruction = decision.get("instruction", "")
        if action == "adjust" and instruction.strip():
            # 用 LLM 重排剩余维度
            new_plan, summary = _adjust_direction(
                state, plan, plan_index, instruction.strip()
            )
            if new_plan:
                logger.decision("analyze_leads", "adjust_direction",
                                reason=summary, detail={"instruction": instruction,
                                                         "new_plan": [d.get("name") for d in new_plan]})
                plan = new_plan
                # 保持 plan_index（用户调整的是剩余维度），但确保不越界
                plan_index = min(plan_index, max(0, len(plan) - 1))
            else:
                logger.decision("analyze_leads", "adjust_failed",
                                reason="重排失败，沿用原计划", detail={"instruction": instruction})
        elif action == "stop":
            logger.decision("analyze_leads", "stop", reason="用户中途停止",
                            detail={"round": round_num})
            return {
                "leads": [l for l in state.get("leads", [])],
                "all_rounds": state.get("all_rounds", []),
                "decision": "stop",
                "progress": _add_progress(state, "analyze_leads", "用户中途停止调查"),
            }

    # 维护线索队列（记录发现，供报告使用）
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
            source_round=round_num,
        ))

    # 当前维度
    current_dim = plan[plan_index] if plan and plan_index < len(plan) else None
    current_dim_name = current_dim.get("name", "未知维度") if current_dim else "未知维度"

    # 记录本轮结果
    all_rounds = list(state.get("all_rounds", []))
    all_rounds.append({
        "round": round_num,
        "dimension": current_dim_name,
        "queries": state.get("search_plan", []),
        "total_leads": len(queue.all()),
        "findings": [f.get("name", "") for f in report.related_entities[-10:]],
    })

    # 判断：是否所有维度执行完，或达到轮数上限
    all_dims_done = (plan_index + 1) >= len(plan)
    rounds_exhausted = round_num >= max_rounds

    if all_dims_done or rounds_exhausted:
        reason = "所有维度执行完毕" if all_dims_done else f"达到轮数上限({max_rounds})"
        logger.decision("analyze_leads", "stop", reason=reason,
                        detail={"plan_index": plan_index, "total_dimensions": len(plan)})
        result: dict = {
            "leads": queue.to_dict_list(),
            "all_rounds": all_rounds,
            "plan": plan,                    # 写回（可能被 P2.3 调整过）
            "decision": "stop",
            "progress": _add_progress(state, "analyze_leads", f"收敛({reason})"),
        }
        return result

    # 进入下一维度
    next_index = plan_index + 1
    next_dim = plan[next_index]
    logger.decision(
        "analyze_leads", "continue", reason="进入下一维度",
        detail={"from": current_dim_name, "to": next_dim.get("name", ""),
                "progress": f"{next_index+1}/{len(plan)}"},
    )
    result: dict = {
        "leads": queue.to_dict_list(),
        "all_rounds": all_rounds,
        "plan": plan,                    # 写回（可能被 P2.3 调整过）
        "plan_index": next_index,
        "search_plan": next_dim.get("queries", [state["entity_name"]]),
        "round": round_num + 1,
        "decision": "continue",
        "progress": _add_progress(
            state, "analyze_leads",
            f"维度完成 [{current_dim_name}] → 进入 [{next_dim.get('name', '')}] "
            f"({next_index+1}/{len(plan)})",
        ),
    }
    return result


# ── P2.2 线索并行 ─────────────────────────────────────

MAX_PARALLEL_LEADS = 3  # 每轮最多并行深挖的线索数（防止资源过载）


def _adjust_direction(
    state: InvestigationState, plan: list[dict], plan_index: int, instruction: str,
) -> tuple[list[dict] | None, str]:
    """P2.3: 用户中途调整方向 → LLM 重排剩余维度。

    返回 (new_plan, summary)。new_plan 为 None 表示调整失败（沿用原计划）。
    """
    try:
        from shared.llm.client import LLMClient

        llm = LLMClient()
        entity = state["entity_name"]
        hints = state.get("hints", "")
        done_dims = [d.get("name", "?") for d in plan[:plan_index]]
        remaining = [
            {"name": d.get("name", ""), "priority": d.get("priority", "medium"),
             "queries": d.get("queries", []), "rationale": d.get("rationale", "")[:200]}
            for d in plan[plan_index:]
        ]

        prompt = f"""调查核心实体: {entity}
用户关注方向: {hints or "（无）"}

【已执行维度】{done_dims or "（无）"}
【当前剩余维度】
{remaining}

【用户新指令】{instruction}

请根据新指令重排/修改【剩余维度】。输出 JSON:
{{
  "dimensions": [
    {{
      "name": "维度名",
      "methodology_source": "引用的方法论来源",
      "rationale": "为什么保留/新增",
      "queries": ["搜索角度1", "搜索角度2"],
      "priority": "high|medium|low"
    }}
  ],
  "summary": "一句话说明调整思路"
}}

规则:
1. 用户指令明确提到的方向 → 新增或提前 + high 优先级
2. 用户指令明确要放弃的方向 → 删除
3. 未涉及的原剩余维度 → 可保留（按相关性排序）
4. 3-7 个维度；基线档案（若在剩余中）保持第一
"""
        data = llm.extract_json(prompt, "请调整调查方向", temperature=0.2, max_tokens=4000)
        dims = [d for d in data.get("dimensions", []) if d.get("name") and d.get("queries")]
        if not dims:
            return None, "LLM 返回空维度"
        return dims, data.get("summary", f"已按指令调整: {len(dims)} 个剩余维度")
    except Exception as e:
        return None, f"调整失败: {e}"


def route_after_analyze(state: InvestigationState) -> list[Send] | str:
    """analyze_leads 后路由：有 high 线索未追 → Send 并行深挖；否则按原逻辑"""
    leads = state.get("leads", [])
    candidates = [
        l for l in leads
        if not l.get("searched") and l.get("priority") == "high"
        and l.get("name") and l.get("name") not in state.get("visited", [])
    ][:MAX_PARALLEL_LEADS]

    if candidates and len(leads) >= 3:  # 线索足够多才值得并行
        return [
            Send("lead_search", {
                "entity_name": state["entity_name"],
                "hints": state.get("hints", ""),
                "goal": state.get("goal", ""),
                "job_id": state.get("job_id", ""),
                "lead_name": c["name"],
                "lead_relation": c.get("relation", ""),
            })
            for c in candidates
        ]

    # 无并行线索 → 原逻辑：维度未完继续搜索，否则 synthesize
    if state.get("decision") == "continue":
        return "search"
    return "synthesize"


def lead_search_node(state: InvestigationState) -> dict:
    """并行深挖单条线索：对该线索名做快速搜索，结果进 parallel_results"""
    from shared.tools.registry import get_registry

    logger = _get_logger(state)
    t0 = time.time()
    lead_name = state.get("lead_name", "")
    if not lead_name:
        return {}

    logger.node_start("lead_search", {"lead": lead_name})
    reg = get_registry()
    try:
        r = reg.call("search_all_fast", job_id=state.get("job_id", ""),
                     query=lead_name, max_per_source=8)
        results = []
        for item in (r.web_results + r.social_results + r.knowledge_results):
            results.append({
                "url": item.url,
                "title": item.title,
                "content": (item.content or "")[:500],
                "source": item.source,
                "source_type": item.source_type,
                "author": item.author,
            })
        logger.node_end("lead_search", {"lead": lead_name, "results": len(results)},
                        duration_ms=(time.time() - t0) * 1000)
        return {
            "parallel_results": [{
                "lead": lead_name,
                "relation": state.get("lead_relation", ""),
                "results": results,
                "total": len(results),
            }],
        }
    except Exception as e:
        logger.node_end("lead_search", {"lead": lead_name, "error": str(e)[:100]},
                        duration_ms=(time.time() - t0) * 1000)
        return {
            "parallel_results": [{
                "lead": lead_name,
                "relation": state.get("lead_relation", ""),
                "results": [],
                "total": 0,
                "error": str(e)[:100],
            }],
        }


def merge_parallel_node(state: InvestigationState) -> dict:
    """合并并行深挖结果到 report（按 URL 去重）+ 标记线索已追"""
    logger = _get_logger(state)
    report = state["report"]
    results = state.get("parallel_results", [])

    # 合并到 report（统一去重）
    seen = {r.url for r in report.web_results + report.social_results + report.knowledge_results}
    added = 0
    lead_names = set()
    for pr in results:
        lead = pr.get("lead", "")
        if lead:
            lead_names.add(lead)
        for item in pr.get("results", []):
            url = item.get("url", "")
            if not url or url in seen:
                continue
            seen.add(url)
            from shared.crawlers.base import SearchResult
            report.web_results.append(SearchResult(
                source=item.get("source", "parallel"),
                source_type=item.get("source_type", "web"),
                url=url,
                title=item.get("title", ""),
                content=item.get("content", ""),
                author=item.get("author", ""),
            ))
            added += 1

    # 标记这些线索已追
    leads = []
    for l in state.get("leads", []):
        if l.get("name") in lead_names:
            l["searched"] = True
        leads.append(l)

    # visited 加入线索名（防环路）
    visited = list(state.get("visited", []))
    for n in lead_names:
        if n not in visited:
            visited.append(n)

    report.total_results = len(
        report.web_results + report.social_results + report.knowledge_results
    )
    logger.info("merge_parallel", f"并行深挖合并 {added} 条新结果",
                detail={"leads": sorted(lead_names), "added": added})

    # 本轮并行结束后：回到 analyze_leads 决定下一步（继续维度/收敛）
    return {
        "report": report,
        "leads": leads,
        "visited": visited,
        "decision": "continue",  # 让路由重新判断（此时 parallel_results 已累积）
        "progress": _add_progress(
            state, "merge_parallel",
            f"线索并行深挖完成: {len(lead_names)} 线索 / 新增 {added} 条结果",
        ),
    }


def synthesize_node(state: InvestigationState) -> dict:
    """信息整合推理 → 最终分析报告（走 Tool Registry，留痕）"""
    from shared.tools.registry import get_registry

    logger = _get_logger(state)
    t0 = time.time()
    report = state["report"]
    analysis = get_registry().call(
        "synthesize_report", job_id=state.get("job_id", ""),
        entity=state["entity_name"], hints=state["hints"], goal=state["goal"],
        report=report,
    )
    logger.node_end("synthesize",
                    {"logical_chains": len(analysis.logical_chains),
                     "findings": len(analysis.key_findings),
                     "timeline": len(analysis.timeline)},
                    duration_ms=(time.time() - t0) * 1000)
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
    """构建 Neo4j 知识图谱（走 Tool Registry，留痕 + P4.2 血统）"""
    from shared.tools.registry import get_registry

    analysis = state["analysis"]
    stats = get_registry().call(
        "graph_build", job_id=state.get("job_id", ""),
        entity_name=state["entity_name"], report=state["report"], analysis=analysis,
        parent_job_id=state.get("parent_job_id", ""),
        parent_entity=state.get("parent_entity", ""),
        parent_relation=state.get("parent_relation", ""),
    )

    return {
        "progress": _add_progress(
            state, "graph_done",
            f"图谱构建完成: {stats['entities']} 实体, {stats['relations']} 关系",
        ),
    }


# ── 路由 ──────────────────────────────────────────────

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
        builder.add_node("plan", plan_node)
        builder.add_node("search", search_node)
        builder.add_node("filter", filter_node)
        builder.add_node("extract", extract_node)
        builder.add_node("deep_audio", deep_audio_node)
        builder.add_node("analyze_leads", analyze_leads_node)
        builder.add_node("lead_search", lead_search_node)
        builder.add_node("merge_parallel", merge_parallel_node)
        builder.add_node("synthesize", synthesize_node)
        builder.add_node("review", review_node)
        builder.add_node("graph_build", graph_build_node)

        builder.add_edge(START, "plan")
        builder.add_edge("plan", "search")
        builder.add_edge("search", "filter")
        builder.add_edge("filter", "extract")
        builder.add_edge("extract", "deep_audio")
        builder.add_edge("deep_audio", "analyze_leads")
        builder.add_conditional_edges(
            "analyze_leads", route_after_analyze,
            {
                "search": "search",
                "synthesize": "synthesize",
                "lead_search": "lead_search",
            },
        )
        # Send 并行分支：lead_search 各分支 → merge_parallel（合并）→ 回 analyze_leads
        builder.add_edge("lead_search", "merge_parallel")
        builder.add_edge("merge_parallel", "analyze_leads")
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
    entity_name: str, hints: str = "", goal: str = "", max_rounds: int = 30,
    job_id: str = "", plan_provider: str = "", adjustable: bool = False,
    parent_job_id: str = "", parent_entity: str = "", parent_relation: str = "",
) -> tuple[dict, str]:
    """执行调查（多轮深挖）到 review 暂停点。返回 (result_state, thread_id)

    adjustable=True 时每轮 analyze_leads interrupt，用户可中途调整方向（P2.3）。
    parent_*: P4.2 图谱递归血统（父调查来源）。
    """
    thread_id = f"inv_{int(time.time()*1000)}"
    config = {"configurable": {"thread_id": thread_id}}

    initial: InvestigationState = {
        "entity_name": entity_name,
        "hints": hints,
        "goal": goal,
        "max_rounds": max_rounds,
        "plan_provider": plan_provider,   # 空 = 用 config.PLAN_PROVIDER
        "plan": [],             # plan_node 填充
        "plan_index": 0,
        "round": 1,
        "search_plan": [entity_name],
        "visited": [],
        "leads": [],
        "all_rounds": [],
        "asr_count": 0,
        "parallel_leads": [],
        "parallel_results": [],
        "adjustable": adjustable,
        "parent_job_id": parent_job_id,
        "parent_entity": parent_entity,
        "parent_relation": parent_relation,
        "report": EntityReport(entity_name=entity_name),
        "analysis": AnalysisReport(entity_name=entity_name),
        "progress": [],
        "error": "",
        "decision": "",
        "job_id": job_id or thread_id,
    }

    # 设置全局 logger 上下文（节点执行时 searcher/llm client 通过它记录代码级日志）
    from shared.utils.logger import InvestigationLogger, set_current_logger
    _logger = InvestigationLogger(job_id=job_id or thread_id, thread_id=thread_id)
    set_current_logger(_logger)

    result = get_graph().invoke(initial, config)
    # 清除全局 logger 上下文（调查结束）
    set_current_logger(None)
    return result, thread_id


def resume_investigation(thread_id: str, action: str, instruction: str = "") -> dict:
    """在暂停点继续调查。

    action:
      approve / reject — review 节点（用户审阅报告后）
      continue         — analyze_leads 节点（P2.3 每轮暂停后继续）
      adjust           — analyze_leads 节点（P2.3 调整方向，instruction 为新指令）
      stop             — analyze_leads 节点（P2.3 中途停止）
    """
    config = {"configurable": {"thread_id": thread_id}}
    if action == "adjust":
        resume = {"action": "adjust", "instruction": instruction}
    elif action in ("continue", "stop"):
        resume = {"action": action}
    else:  # approve / reject
        resume = {"action": action}
    result = get_graph().invoke(Command(resume=resume), config)
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

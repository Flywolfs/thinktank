"""信息整合推理器 — 将筛选后的元数据 + 抽取的实体整合为完整逻辑链路

流水线位置: search → RelevanceFilter → EntityExtractor → 【Synthesizer】 → 报告 → HITL → 图谱

核心职责:
1. 把零散的搜索结果、实体、关系整合成"完整的思考推理链路"
2. 在"忠于事实"原则下推理（区分 [原文]/[推断]/[存疑]）
3. 生成用户可读的报告 + 图谱构建建议
4. 报告返回用户审阅，由用户决定是否构建知识图谱（HITL）
"""

import json
from pathlib import Path

from shared.llm.client import LLMClient
from shared.models.analysis_report import (
    AnalysisReport, Finding, LogicalChain, LogicalLink, TimelineEvent,
)
from shared.models.entity_report import EntityReport

# 加载调查方法论作为 system prompt 的一部分
_METHODOLOGY_PATH = Path(__file__).parent / "INVESTIGATION_METHODOLOGY.md"


def _load_methodology() -> str:
    """加载方法论文档（如果存在）"""
    try:
        return _METHODOLOGY_PATH.read_text(encoding="utf-8")
    except Exception:
        return ""

SYNTHESIS_PROMPT = """你是一名资深情报分析师。基于以下已收集的信息（搜索摘要 + 实体抽取结果），
为调查目标生成一份完整的情报分析报告。

调查目标: {entity_name}
用户关注: {hints}
调查意图: {goal}

【已收集的搜索结果摘要】
{search_results}

【已抽取的实体关系】
{extracted_entities}

请整合以上原子化信息，推理出完整的逻辑链路，输出 JSON（不要输出其他文字）:
{{
  "entity_summary": "实体概述（基线档案，2-3句话）",
  "logical_chains": [
    {{
      "title": "链路标题（一句话概括这条推理线）",
      "summary": "链路概述",
      "links": [
        {{
          "step": 1,
          "premise": "前提（已核实的事实/证据）",
          "inference": "推理（从前提推出的结论）",
          "confidence": "原文|推断|存疑",
          "basis": "依据（哪些来源/证据支撑）"
        }}
      ],
      "conclusion": "链路最终结论",
      "confidence": "高|中|低"
    }}
  ],
  "key_findings": [
    {{
      "title": "发现标题",
      "summary": "发现摘要",
      "confidence": "高|中|低|存疑",
      "evidence": "支撑证据",
      "sources": ["来源URL"],
      "tags": ["标签"]
    }}
  ],
  "timeline": [
    {{"date": "YYYY-MM-DD 或 YYYY 或 不详", "description": "事件描述", "sources": ["来源URL"]}}
  ],
  "pending_verification": ["待验证线索1", "待验证线索2"],
  "suggested_entities": [{{"name": "实体名", "type": "person|organization|event"}}],
  "suggested_relations": [{{"from": "实体A", "to": "实体B", "relation": "关系描述"}}]
}}

严格遵循以下原则:
1. 【忠于事实】区分三类信息: [原文]=直接来自来源的表述; [推断]=基于多个来源合理推导（必须说明依据）;
   [存疑]=来源冲突或无法验证。不得把推断当事实陈述。
2. 逻辑链路必须有完整推理链: 前提→推理→结论，每一步说明依据。
3. 没有证据支撑的猜测不要写，或标为[存疑]放进 pending_verification。
4. 时间线按时间排序，标注来源。
5. 图谱建议基于已抽出的实体关系，不要发明不存在的实体。"""


class Synthesizer:
    """信息整合推理器"""

    def __init__(self, llm: LLMClient | None = None):
        self.llm = llm or LLMClient()
        self.methodology = _load_methodology()

    def synthesize(
        self,
        report: EntityReport,
        hints: str = "",
        goal: str = "",
    ) -> AnalysisReport:
        """
        将 EntityReport（已筛选 + 已抽取）整合为完整分析报告。

        Args:
            report: 已过滤并完成实体抽取的 EntityReport
            hints: 用户关注方向
            goal: 调查意图

        Returns:
            AnalysisReport: 完整报告（含逻辑链路、关键发现、时间线、图谱建议）
        """
        search_results = self._format_search_results(report)
        extracted = self._format_extracted(report)

        user_input = SYNTHESIS_PROMPT.format(
            entity_name=report.entity_name,
            hints=hints or "（无）",
            goal=goal or self._infer_goal(report.entity_name, hints),
            search_results=search_results,
            extracted_entities=extracted,
        )

        # 附加方法论作为 system 上下文（如果存在）
        system_context = ""
        if self.methodology:
            system_context = (
                "以下是你的调查方法论，请遵循其中的忠实原则和输出规范:\n"
                f"{self.methodology[:8000]}\n\n"
            )

        try:
            data = self.llm.extract_json(system_context, user_input, temperature=0.2)
        except Exception as e:
            # LLM 失败：返回最小报告
            return AnalysisReport(
                entity_name=report.entity_name,
                goal=hints or "",
                entity_summary=report.core_summary,
                pending_verification=[f"报告生成失败: {e}"],
            )

        return self._parse_report(data, report, hints, goal)

    # ── 解析 ───────────────────────────────────────────

    def _parse_report(
        self, data: dict, report: EntityReport, hints: str, goal: str
    ) -> AnalysisReport:
        ar = AnalysisReport(
            entity_name=report.entity_name,
            goal=goal or hints or "",
            entity_summary=data.get("entity_summary", report.core_summary),
        )

        # 逻辑链路
        for chain_data in data.get("logical_chains", []) or []:
            links = [
                LogicalLink(
                    step=l.get("step", i + 1),
                    premise=l.get("premise", ""),
                    inference=l.get("inference", ""),
                    confidence=l.get("confidence", "推断"),
                    basis=l.get("basis", ""),
                )
                for i, l in enumerate(chain_data.get("links", []) or [])
            ]
            ar.logical_chains.append(LogicalChain(
                title=chain_data.get("title", "未命名链路"),
                summary=chain_data.get("summary", ""),
                links=links,
                conclusion=chain_data.get("conclusion", ""),
                confidence=chain_data.get("confidence", "中"),
            ))

        # 关键发现
        for f in data.get("key_findings", []) or []:
            ar.key_findings.append(Finding(
                title=f.get("title", ""),
                summary=f.get("summary", ""),
                confidence=f.get("confidence", "存疑"),
                evidence=f.get("evidence", ""),
                sources=f.get("sources", []) or [],
                tags=f.get("tags", []) or [],
            ))

        # 时间线
        for t in data.get("timeline", []) or []:
            ar.timeline.append(TimelineEvent(
                date=t.get("date", "不详"),
                description=t.get("description", ""),
                sources=t.get("sources", []) or [],
            ))

        ar.pending_verification = data.get("pending_verification", []) or []
        ar.suggested_entities = data.get("suggested_entities", []) or []
        ar.suggested_relations = data.get("suggested_relations", []) or []

        # 来源汇总
        sources = set()
        for r in report.web_results + report.social_results + report.knowledge_results:
            if r.url:
                sources.add(r.url)
        ar.sources = list(sources)[:20]

        return ar

    # ── 格式化输入 ─────────────────────────────────────

    @staticmethod
    def _format_search_results(report: EntityReport) -> str:
        lines = []
        for i, r in enumerate(report.web_results[:10]):
            lines.append(f"[网页{i}] {r.title}\n  {r.url}\n  {(r.content or '')[:150]}")
        for i, r in enumerate(report.social_results[:10]):
            lines.append(f"[社交{i}] {r.title} (by {r.author})\n  {r.url}\n  {(r.content or '')[:100]}")
        for i, r in enumerate(report.knowledge_results[:3]):
            lines.append(f"[知识{i}] {r.title}\n  {(r.content or '')[:300]}")
        return "\n\n".join(lines) or "（无搜索结果）"

    @staticmethod
    def _format_extracted(report: EntityReport) -> str:
        if not report.related_entities:
            return "（无实体抽取结果）"
        lines = []
        for e in report.related_entities:
            lines.append(
                f"- {e.get('name', '')} ({e.get('type', '')}) --[{e.get('relation', '')}]--> "
                f"{report.entity_name} [方向:{e.get('direction', 'outgoing')}]"
            )
        return "\n".join(lines)

    @staticmethod
    def _infer_goal(entity_name: str, hints: str) -> str:
        if hints:
            return f"围绕'{entity_name}'调查: {hints}"
        return f"全面调查'{entity_name}'"


# ==================== 自检 ====================
if __name__ == "__main__":
    from entity_intel.searcher import EntitySearcher

    print("Synthesizer 自检")
    print("=" * 40)

    searcher = EntitySearcher()
    report = searcher.search("雷军", max_per_source=5)

    # 完整流水线: 搜索 → 筛选 → 抽取 → 整合
    searcher.filter_results(report, hints="小米汽车")
    searcher.extract_entities(report)

    syn = Synthesizer()
    ar = syn.synthesize(report, hints="小米汽车")

    print(f"实体: {ar.entity_name}")
    print(f"概述: {ar.entity_summary[:100]}...")
    print(f"逻辑链路: {len(ar.logical_chains)} 条")
    for c in ar.logical_chains:
        print(f"  - {c.title} [{c.confidence}] ({len(c.links)}步)")
    print(f"关键发现: {len(ar.key_findings)} 条")
    print(f"时间线: {len(ar.timeline)} 个事件")
    print(f"待验证: {len(ar.pending_verification)} 条")
    print(f"图谱建议: {len(ar.suggested_entities)} 实体, {len(ar.suggested_relations)} 关系")

    print("\n--- Markdown 报告预览 ---")
    md = ar.to_markdown()
    print(md[:800])

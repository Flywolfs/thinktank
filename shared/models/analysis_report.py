"""情报分析报告模型 — 信息整合推理阶段的输出"""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Finding:
    """一条关键发现"""
    title: str                          # 发现标题
    summary: str                        # 摘要
    confidence: str = "存疑"             # 高 / 中 / 低 / 存疑
    evidence: str = ""                  # 支撑证据（原文引用/来源简述）
    sources: list[str] = field(default_factory=list)  # 来源 URL
    tags: list[str] = field(default_factory=list)     # 标签


@dataclass
class LogicalLink:
    """逻辑链路中的一环（推理步骤）"""
    step: int                           # 步骤序号
    premise: str                        # 前提（已核实的事实/证据）
    inference: str                      # 推理（从前提推出的结论）
    confidence: str = "推断"             # 原文 / 推断 / 存疑
    basis: str = ""                     # 依据（哪些来源/证据支撑）


@dataclass
class LogicalChain:
    """一条完整逻辑链路（核心产出）"""
    title: str                          # 链路标题（一句话概括这条推理线）
    summary: str                        # 链路概述
    links: list[LogicalLink] = field(default_factory=list)
    conclusion: str = ""                # 链路的最终结论
    confidence: str = "推断"             # 整条链路的置信度


@dataclass
class TimelineEvent:
    """时间线上的一个事件"""
    date: str                           # YYYY-MM-DD 或 YYYY 或 "不详"
    description: str
    sources: list[str] = field(default_factory=list)


@dataclass
class AnalysisReport:
    """信息整合推理的完整报告"""
    entity_name: str
    generated_at: datetime = field(default_factory=datetime.now)
    goal: str = ""                      # 本次调查目标/hints

    # 报告主体
    entity_summary: str = ""            # 实体概述（基线档案）
    logical_chains: list[LogicalChain] = field(default_factory=list)
    key_findings: list[Finding] = field(default_factory=list)
    timeline: list[TimelineEvent] = field(default_factory=list)
    pending_verification: list[str] = field(default_factory=list)  # 待验证线索

    # 图谱建议（供 HITL 决策）
    suggested_entities: list[dict] = field(default_factory=list)   # [{name, type}]
    suggested_relations: list[dict] = field(default_factory=list)  # [{from, to, relation}]

    # 附加
    sources: list[str] = field(default_factory=list)
    raw_metadata: dict = field(default_factory=dict)  # 原始搜索结果摘要

    def to_dict(self) -> dict:
        return {
            "entity_name": self.entity_name,
            "generated_at": self.generated_at.isoformat(),
            "goal": self.goal,
            "entity_summary": self.entity_summary,
            "logical_chains": [
                {
                    "title": c.title,
                    "summary": c.summary,
                    "links": [
                        {
                            "step": l.step,
                            "premise": l.premise,
                            "inference": l.inference,
                            "confidence": l.confidence,
                            "basis": l.basis,
                        } for l in c.links
                    ],
                    "conclusion": c.conclusion,
                    "confidence": c.confidence,
                } for c in self.logical_chains
            ],
            "key_findings": [
                {
                    "title": f.title,
                    "summary": f.summary,
                    "confidence": f.confidence,
                    "evidence": f.evidence,
                    "sources": f.sources,
                    "tags": f.tags,
                } for f in self.key_findings
            ],
            "timeline": [
                {"date": t.date, "description": t.description, "sources": t.sources}
                for t in self.timeline
            ],
            "pending_verification": self.pending_verification,
            "suggested_entities": self.suggested_entities,
            "suggested_relations": self.suggested_relations,
            "sources": self.sources,
        }

    def to_markdown(self) -> str:
        """渲染为可读的 Markdown 报告（给用户查看）"""
        lines = [
            f"# 情报分析报告: {self.entity_name}",
            f"",
            f"> 调查目标: {self.goal or '全面调查'}",
            f"> 生成时间: {self.generated_at.strftime('%Y-%m-%d %H:%M')}",
            f"",
            f"## 一、实体概述",
            f"",
            self.entity_summary or "（暂无概述）",
            f"",
            f"## 二、逻辑推理链路",
            f"",
        ]

        if not self.logical_chains:
            lines.append("（暂无推理链路）")
        for i, chain in enumerate(self.logical_chains, 1):
            lines.append(f"### 链路 {i}: {chain.title}")
            lines.append(f"")
            lines.append(f"*{chain.summary}*")
            lines.append(f"")
            lines.append(f"**置信度: {chain.confidence}**")
            lines.append(f"")
            for link in chain.links:
                lines.append(f"**{link.step}. [{link.confidence}]**")
                lines.append(f"- 前提: {link.premise}")
                lines.append(f"- 推理: {link.inference}")
                if link.basis:
                    lines.append(f"- 依据: {link.basis}")
            lines.append(f"")
            if chain.conclusion:
                lines.append(f"**结论: {chain.conclusion}**")
                lines.append(f"")

        lines += [
            f"## 三、关键发现",
            f"",
        ]
        if not self.key_findings:
            lines.append("（暂无关键发现）")
        for f in self.key_findings:
            lines.append(f"### [{f.confidence}] {f.title}")
            lines.append(f"{f.summary}")
            if f.evidence:
                lines.append(f"")
                lines.append(f"> 证据: {f.evidence}")
            if f.sources:
                lines.append(f"")
                lines.append(f"来源: {' | '.join(f.sources[:3])}")
            lines.append(f"")

        lines += [
            f"## 四、时间线",
            f"",
        ]
        if not self.timeline:
            lines.append("（暂无时间线）")
        for t in sorted(self.timeline, key=lambda x: x.date):
            lines.append(f"- **{t.date}**: {t.description}")

        lines += [
            f"",
            f"## 五、待验证线索",
            f"",
        ]
        if not self.pending_verification:
            lines.append("（暂无待验证线索）")
        for p in self.pending_verification:
            lines.append(f"- ⚠️ {p}")

        lines += [
            f"",
            f"## 六、图谱构建建议",
            f"",
            f"**建议实体 ({len(self.suggested_entities)}):** "
            + ", ".join(e.get("name", "") for e in self.suggested_entities[:10]),
            f"",
            f"**建议关系 ({len(self.suggested_relations)}):**",
        ]
        for r in self.suggested_relations[:10]:
            lines.append(
                f"- {r.get('from', '')} --[{r.get('relation', '')}]--> {r.get('to', '')}"
            )

        return "\n".join(lines)

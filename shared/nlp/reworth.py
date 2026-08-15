"""ReinvestigationScorer — 重挖评分器（P4.2）

系统自动判断"是否值得重挖某实体"：
1. 新线索强度: 新调查结果中提及该实体的条数
2. 新关系类型数: 新调查中涉及该实体的新关系种类
3. 与旧调查的重叠度: 新发现与已有图谱信息的重叠（低重叠=高价值）

硬上限: 同一实体最多自动重挖 3 次（超过必须用户手动确认）
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ReworthScore:
    """重挖价值评分"""
    score: float                  # 0.0 - 1.0
    mention_count: int            # 新调查中提到该实体的条数
    new_relation_types: int       # 新关系类型数
    overlap_ratio: float          # 与旧信息重叠度（0=全新, 1=完全重叠）
    reason: str = ""              # 决策理由
    should_reworth: bool = False  # 系统是否建议重挖

    def to_dict(self) -> dict:
        return {
            "score": round(self.score, 2),
            "mention_count": self.mention_count,
            "new_relation_types": self.new_relation_types,
            "overlap_ratio": round(self.overlap_ratio, 2),
            "reason": self.reason,
            "should_reworth": self.should_reworth,
        }


class ReworthScorer:
    # 参数（可调）
    MIN_MENTIONS = 3              # 至少提到 N 条才考虑重挖
    MIN_NEW_RELATIONS = 1         # 至少 1 个新关系类型
    MAX_AUTO_REWORTH = 3          # 自动重挖硬上限

    def score(
        self,
        entity: str,
        mention_count: int,
        new_relation_types: int,
        overlap_ratio: float,
        investigated_count: int,
    ) -> ReworthScore:
        """计算重挖价值。

        Args:
            mention_count: 新调查结果中提及该实体的条数
            new_relation_types: 新调查中涉及该实体的新关系类型数
            overlap_ratio: 与旧图谱信息的重叠度（0-1）
            investigated_count: 已调查次数（自动重挖上限判断）
        """
        score = 0.0
        reasons = []

        # 1. 提及量（0-0.4）
        if mention_count >= self.MIN_MENTIONS:
            score += min(0.4, 0.1 + mention_count * 0.05)
            reasons.append(f"新调查提到 {mention_count} 条")
        else:
            reasons.append(f"提及不足({mention_count}<{self.MIN_MENTIONS})")

        # 2. 新关系类型（0-0.3）
        if new_relation_types >= self.MIN_NEW_RELATIONS:
            score += min(0.3, new_relation_types * 0.15)
            reasons.append(f"{new_relation_types} 个新关系")
        else:
            reasons.append("无新关系")

        # 3. 重叠度（0-0.3，低重叠=高价值）
        novelty = 1.0 - overlap_ratio
        score += novelty * 0.3
        if novelty > 0.5:
            reasons.append(f"新信息占比高({novelty:.0%})")
        else:
            reasons.append(f"与旧信息重叠({overlap_ratio:.0%})")

        # 硬上限: 已调查次数 >= MAX → 强制不自动
        if investigated_count >= self.MAX_AUTO_REWORTH:
            reasons.append(f"已达自动重挖上限({self.MAX_AUTO_REWORTH}次)")
            return ReworthScore(
                score=score, mention_count=mention_count,
                new_relation_types=new_relation_types, overlap_ratio=overlap_ratio,
                reason="; ".join(reasons), should_reworth=False,
            )

        should = score >= 0.5
        return ReworthScore(
            score=score, mention_count=mention_count,
            new_relation_types=new_relation_types, overlap_ratio=overlap_ratio,
            reason="; ".join(reasons), should_reworth=should,
        )

    # ── 从搜索结果估算 ─────────────────────────────────

    def score_from_results(
        self,
        entity: str,
        results: list,             # list[SearchResult] 或 dict（title/content 字段）
        known_entities: set,       # 图谱已有实体集合（归一化 key）
        investigated_count: int,
    ) -> ReworthScore:
        """从新调查的搜索结果估算重挖价值（不依赖图谱查询）"""
        import re

        mentions = 0
        new_relations = 0
        overlap_count = 0
        total = max(1, len(results))

        # 收集涉及该实体的结果，估算新关系/重叠
        related = []
        for r in results:
            title = getattr(r, "title", "") or (r.get("title", "") if isinstance(r, dict) else "")
            content = getattr(r, "content", "") or (r.get("content", "") if isinstance(r, dict) else "")
            text = f"{title} {content}"
            if entity and entity in text:
                mentions += 1
                related.append(text)

        # 从提及内容中粗略数"新关系"（"XX 的 XXX" 模式或关键关系词）
        relation_keywords = ["创始人", "CEO", "投资", "资助", "控股", "合作", "朋友",
                             "导师", "校友", "同窗", "竞争对手", "合作伙伴", "资金",
                             "股权", "关联", "旗下", "子公司", "董事长", "顾问"]
        for text in related[:50]:
            for kw in relation_keywords:
                if kw in text:
                    new_relations += 1
                    break  # 每条结果最多算 1 个新关系

        # 重叠度: 提及内容中命中国谱已有实体的比例（粗略）
        for text in related[:50]:
            hit = any(known in text for known in known_entities if known)
            if hit:
                overlap_count += 1
        overlap_ratio = overlap_count / max(1, len(related)) if related else 0.0

        return self.score(
            entity=entity,
            mention_count=mentions,
            new_relation_types=min(new_relations, 5),
            overlap_ratio=overlap_ratio,
            investigated_count=investigated_count,
        )


_default_scorer: ReworthScorer | None = None


def get_scorer() -> ReworthScorer:
    global _default_scorer
    if _default_scorer is None:
        _default_scorer = ReworthScorer()
    return _default_scorer

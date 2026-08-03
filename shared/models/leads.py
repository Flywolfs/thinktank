"""线索数据模型 — Phase B 多轮深挖的线索队列

线索 = 调查中发现的新实体/事件/疑点，供下一轮搜索深挖。
优先级高的线索先追，已访问的实体防环路。
"""

from dataclasses import dataclass, field


@dataclass
class Lead:
    """一条待追线索"""
    name: str                # 线索实体名/关键词（下一轮搜索用）
    relation: str = ""       # 与核心实体的关系
    priority: str = "medium" # high / medium / low
    reason: str = ""         # 为什么值得追
    source_round: int = 1    # 哪一轮发现的
    searched: bool = False   # 是否已追过

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "relation": self.relation,
            "priority": self.priority,
            "reason": self.reason,
            "source_round": self.source_round,
            "searched": self.searched,
        }


class LeadQueue:
    """线索队列 — 按优先级排序，支持去重和环路防护"""

    def __init__(self):
        self._leads: list[Lead] = []

    def add(self, lead: Lead):
        """添加线索（去重：同名且同关系的不重复加）"""
        for existing in self._leads:
            if existing.name == lead.name and existing.relation == lead.relation:
                return
        self._leads.append(lead)

    def add_many(self, leads: list[Lead]):
        for l in leads:
            self.add(l)

    def next(self) -> Lead | None:
        """取最高优先级未搜索的线索"""
        candidates = [l for l in self._leads if not l.searched]
        if not candidates:
            return None
        priority_order = {"high": 0, "medium": 1, "low": 2}
        candidates.sort(key=lambda l: priority_order.get(l.priority, 1))
        return candidates[0]

    def mark_searched(self, name: str):
        for l in self._leads:
            if l.name == name:
                l.searched = True

    def has_pending(self) -> bool:
        return any(not l.searched for l in self._leads)

    def all(self) -> list[Lead]:
        return self._leads

    def to_dict_list(self) -> list[dict]:
        return [l.to_dict() for l in self._leads]

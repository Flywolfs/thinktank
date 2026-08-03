"""通用相关性筛选器 — 从海量搜索结果中分辨有用与噪声

实现 INVESTIGATION_METHODOLOGY.md v5.0 的"相关性筛选"章节。
通用类，供实体情报 / 社会情报 / 金融情报三个子系统复用：
1. 两阶段过滤：规则粗筛 + LLM 精筛打分
2. 四类内容角色：core_owner / about / mention / meme
3. 信息增量去重
4. 线索触发标记

用法（通用）:
    filter = RelevanceFilter()
    outcome = filter.filter_results(results, subject="雷军",
                                    hints="小米汽车", goal="调查商业版图")
    kept = outcome.kept        # list[FilteredItem]
    dropped = outcome.dropped  # list[FilteredItem]
"""

from dataclasses import dataclass, field

from shared.crawlers.base import SearchResult
from shared.llm.client import LLMClient


@dataclass
class FilteredItem:
    """一条经过筛选的结果"""
    result: SearchResult
    relevance: float = 0.0      # 1-10 与调查目标相关度
    role: str = "about"         # core_owner / about / mention / meme
    info_value: float = 0.0     # 1-10 信息增量
    keep: bool = False
    reason: str = ""
    leads: list[str] = field(default_factory=list)  # 触发的新线索


@dataclass
class FilterOutcome:
    """一次筛选的完整结果"""
    kept: list[FilteredItem] = field(default_factory=list)
    dropped: list[FilteredItem] = field(default_factory=list)
    stats: dict = field(default_factory=dict)   # input/coarse_kept/final_kept
    leads: list[str] = field(default_factory=list)


# 规则粗筛：明确排除的关键词（娱乐/玩梗信号，除非目标是舆情）
MEME_KEYWORDS = [
    "鬼畜", "二创", "搞笑", "沙雕", "整活", "梗", "段子",
    "表情包", "玩梗", "恶搞", "鬼畜区", "memes",
]

# 低质量来源信号（搬运/营销）
LOW_QUALITY_SOURCES = [
    "搬运", "营销号", "剪辑", "合集", "混剪",
]


class RelevanceFilter:
    """两阶段相关性筛选器（通用）"""

    def __init__(self, llm: LLMClient | None = None):
        self.llm = llm

    def filter_results(
        self,
        results: list[SearchResult],
        subject: str,
        hints: str = "",
        goal: str = "",
        use_llm: bool = True,
    ) -> FilterOutcome:
        """
        对一批搜索结果执行相关性筛选。

        Args:
            results: 待筛选的搜索结果列表
            subject: 调查对象（实体名 / 话题 / 标的）
            hints: 用户关注方向（如"关注慈善基金会和捐款明细"）
            goal: 调查意图描述（如"调查人物商业版图"）；空则自动推断
            use_llm: 是否启用 LLM 精筛（False = 只做规则粗筛，测试用）

        Returns:
            FilterOutcome: 保留/丢弃的结果 + 统计 + 触发线索
        """
        self.hints = hints
        self.subject = subject
        self.goal = goal or self._infer_goal(subject, hints)

        # 第一阶段：规则粗筛
        coarse_kept: list[FilteredItem] = []
        coarse_dropped: list[FilteredItem] = []
        for r in results:
            item = self._coarse_filter(r)
            if item.keep:
                coarse_kept.append(item)
            else:
                coarse_dropped.append(item)

        # 第二阶段：LLM 精筛
        if use_llm and len(coarse_kept) > 1:
            try:
                coarse_kept = self._llm_fine_filter(coarse_kept)
            except Exception as e:
                # LLM 失败：回退规则粗筛结果，把错误放进 stats
                pass

        kept = [i for i in coarse_kept if i.keep]
        dropped = coarse_dropped + [i for i in coarse_kept if not i.keep]
        leads = [l for i in kept for l in i.leads]

        stats = {
            "input": len(results),
            "coarse_kept": len(coarse_kept),
            "final_kept": len(kept),
            "dropped": len(dropped),
        }

        return FilterOutcome(kept=kept, dropped=dropped, stats=stats, leads=leads)

    # ── 第一阶段：规则粗筛 ──────────────────────────────

    def _coarse_filter(self, r: SearchResult) -> FilteredItem:
        item = FilteredItem(result=r)
        title = r.title or ""
        content = (r.content or "")[:200]

        # 1. 明显噪声：纯娱乐/鬼畜（除非目标含"舆情/形象"）
        target_is_publicity = any(k in self.goal for k in ["舆情", "形象", "舆论", "口碑"])
        if not target_is_publicity:
            for kw in MEME_KEYWORDS:
                if kw in title:
                    item.reason = f"玩梗/娱乐内容: {kw}"
                    return item
            for kw in LOW_QUALITY_SOURCES:
                if kw in title:
                    item.relevance = 2
                    item.reason = f"疑似低质量来源: {kw}"
                    return item

        # 2. 判断角色
        entity = self.subject or ""
        if entity:
            entity_in_title = entity in title
            title_starts_with_entity = title.startswith(entity) or title[:20].find(entity) == 0
            if title_starts_with_entity:
                item.role = "about"
            elif entity_in_title:
                item.role = "about" if len(entity) >= 4 else "mention"
            else:
                item.role = "mention"
        else:
            item.role = "mention"

        # 3. 来源是否为调查对象本人（core_owner）
        author = r.author or ""
        if entity and entity in author:
            item.role = "core_owner"
            item.relevance = 9

        # 4. hints 关键词加权
        if self.hints:
            for kw in self.hints.replace("，", ",").replace("和", ",").split(","):
                kw = kw.strip()
                if kw and kw in (title + content):
                    item.relevance += 3

        # 5. 默认分数
        role_base = {"core_owner": 9, "about": 7, "mention": 4, "meme": 1}
        item.relevance = max(item.relevance, role_base.get(item.role, 5))

        # 粗筛保留：角色非 meme 且 relevance >= 5
        item.keep = item.relevance >= 5
        if not item.keep:
            item.reason = f"相关性低 (role={item.role}, score={item.relevance})"
        return item

    # ── 第二阶段：LLM 精筛 ──────────────────────────────

    def _llm_fine_filter(self, items: list[FilteredItem]) -> list[FilteredItem]:
        """批量给 LLM 打分，更新 relevance/role/info_value/keep/leads"""
        if not self.llm:
            self.llm = LLMClient()

        # 构造输入
        lines = []
        for i, item in enumerate(items):
            r = item.result
            lines.append(
                f"[{i}] title: {r.title}\n"
                f"    source: {r.source} | author: {r.author}\n"
                f"    snippet: {(r.content or '')[:150]}"
            )
        user_input = "\n".join(lines)

        prompt = f"""
调查对象: {self.subject}
调查意图: {self.goal}
用户关注: {self.hints}

以下是搜索结果（编号 0..{len(items)-1}）。请逐条评估，输出 JSON 数组：
[
  {{
    "index": 0,
    "relevance": 1-10,          # 与调查意图/用户关注的相关度
    "role": "core_owner|about|mention|meme",
    "info_value": 1-10,         # 信息增量：是否提供新事实（1=重复已知，10=全新关键事实）
    "keep": true/false,         # 默认 relevance>=7 且 info_value>=5
    "reason": "一句话理由",
    "leads": ["新线索1", "新线索2"]   # 该结果触发的新实体/事件/疑点，无则空数组
  }}
]
规则：
- 多条讲同一件事的结果，只保留 info_value 最高的 1-2 条，其余 keep=false（重复）
- 玩梗/鬼畜/纯娱乐：如果调查意图是舆情/形象分析则保留（作为舆情信号），否则排除
- 顺带提及（mention）通常排除，除非信息增量很高
"""

        try:
            data = self.llm.extract_json(prompt, user_input, temperature=0.0)
            if isinstance(data, dict):
                data = data.get("results", data.get("items", []))
            verdicts = {v["index"]: v for v in data if isinstance(v, dict)}
        except Exception:
            return items  # LLM 失败回退规则结果

        for idx, item in enumerate(items):
            verdict = verdicts.get(idx)
            if not verdict:
                continue
            item.relevance = float(verdict.get("relevance", item.relevance))
            item.role = verdict.get("role", item.role)
            item.info_value = float(verdict.get("info_value", item.info_value))
            item.reason = verdict.get("reason", item.reason)
            item.leads = verdict.get("leads", []) or []
            item.keep = bool(verdict.get("keep", item.keep))

        return items

    # ── 工具 ────────────────────────────────────────────

    def _infer_goal(self, subject: str, hints: str) -> str:
        """从调查对象和 hints 推断调查意图"""
        if hints:
            return f"围绕'{subject}'调查: {hints}"
        return f"全面调查'{subject}'"


# ==================== 自检 ====================
if __name__ == "__main__":
    print("RelevanceFilter (shared/nlp) 自检")
    print("=" * 40)

    # 构造几条假结果验证通用性（不依赖网络）
    from datetime import datetime
    fake_results = [
        SearchResult(source="test", source_type="social", url="u1",
                     title="雷军实测小米汽车", author="雷军",
                     content="车内空间实测"),
        SearchResult(source="test", source_type="social", url="u2",
                     title="雷军跳舞纯享版", author="蔡锌沐",
                     content="鬼畜"),
        SearchResult(source="test", source_type="web", url="u3",
                     title="雷军背景介绍", author="",
                     content="小米创始人"),
        SearchResult(source="test", source_type="web", url="u4",
                     title="我用了小米手机感觉不错", author="路人",
                     content="顺带提及"),
    ]

    f = RelevanceFilter()
    outcome = f.filter_results(fake_results, subject="雷军",
                               hints="小米汽车", use_llm=False)
    print(f"输入: {outcome.stats['input']} → 保留: {outcome.stats['final_kept']}")
    for item in outcome.kept:
        print(f"  ✅ [{item.role}] {item.result.title[:40]}")
    for item in outcome.dropped:
        print(f"  ❌ [{item.role}] {item.result.title[:40]} ({item.reason})")

"""实体情报的相关性筛选薄适配层

复用 shared/nlp/relevance.py 的通用 RelevanceFilter，
把 EntityReport 的三个结果列表合并传入，按 source_type 分回。

用法:
    from entity_intel.relevance import filter_entity_report
    filter_entity_report(report, hints="小米汽车")
"""

from shared.models.entity_report import EntityReport
from shared.nlp.relevance import RelevanceFilter


def filter_entity_report(
    report: EntityReport,
    hints: str = "",
    goal: str = "",
    use_llm: bool = True,
) -> EntityReport:
    """
    对 EntityReport 执行相关性筛选，原地更新三个结果列表。

    Args:
        report: 搜索后的 EntityReport
        hints: 用户关注方向
        goal: 调查意图描述；空则自动推断
        use_llm: 是否启用 LLM 精筛
    """
    all_results = (
        report.web_results
        + report.knowledge_results
        + report.social_results
    )

    outcome = RelevanceFilter().filter_results(
        results=all_results,
        subject=report.entity_name,
        hints=hints,
        goal=goal,
        use_llm=use_llm,
    )

    # 按 source_type 把保留结果分回三类
    report.web_results = _filter_by_type(outcome.kept, "web")
    report.knowledge_results = _filter_by_type(outcome.kept, "knowledge")
    report.social_results = _filter_by_type(outcome.kept, "social")

    # 统计 + 线索写入 report.metadata
    report.metadata["filtered"] = {
        **outcome.stats,
        "leads": outcome.leads,
    }
    if outcome.leads:
        report.metadata["leads"] = outcome.leads

    return report


def _filter_by_type(items, kind: str):
    """按 source_type 过滤保留项"""
    if kind == "knowledge":
        return [i.result for i in items if i.result.source_type == "knowledge"]
    if kind == "social":
        return [i.result for i in items if i.result.source_type == "social"]
    return [
        i.result for i in items
        if i.result.source_type not in ("knowledge", "social")
    ]


# ==================== 自检 ====================
if __name__ == "__main__":
    from entity_intel.searcher import EntitySearcher

    print("filter_entity_report 端到端自检")
    print("=" * 40)

    searcher = EntitySearcher()
    report = searcher.search("雷军", max_per_source=10)
    print(f"搜索完成: 共 {report.total_results} 条")

    filter_entity_report(report, hints="小米汽车")
    meta = report.metadata.get("filtered", {})
    print(f"筛选: {meta.get('input',0)} → 保留 {meta.get('final_kept',0)}")
    print(f"触发线索: {meta.get('leads', [])}")

    print("\n保留的 B站视频:")
    for r in report.social_results[:5]:
        print(f"  [{r.author}] {r.title[:55]}")

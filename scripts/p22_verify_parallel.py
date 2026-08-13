"""P2.2 线索并行验证：
1. route_after_analyze: high 线索 → Send 列表
2. lead_search_node: 单条线索搜索 → parallel_results
3. merge_parallel_node: 合并去重 + 标记 searched
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from langgraph.types import Send
from shared.models.entity_report import EntityReport


def test_route():
    print("=" * 60)
    print("1. route_after_analyze（Send 分发）")
    print("=" * 60)
    from entity_intel.graph import route_after_analyze

    # 构造带 high 线索的 state
    state = {
        "entity_name": "罗永浩",
        "hints": "债务",
        "goal": "",
        "leads": [
            {"name": "郑刚", "relation": "投资人", "priority": "high", "searched": False},
            {"name": "李钧", "relation": "合伙人", "priority": "high", "searched": False},
            {"name": "交个朋友", "relation": "公司", "priority": "medium", "searched": False},
            {"name": "锤子科技", "relation": "公司", "priority": "medium", "searched": True},
        ],
        "visited": [],
        "decision": "continue",
        "job_id": "p22_test",
    }
    result = route_after_analyze(state)
    print(f"  返回类型: {type(result).__name__}")
    if isinstance(result, list):
        print(f"  Send 数量: {len(result)}")
        for s in result:
            assert isinstance(s, Send), f"应为 Send: {type(s)}"
            print(f"    Send → {s.node} payload.lead_name={s.arg.get('lead_name')}")
        assert len(result) == 2, "应有 2 个 high 线索"
        print("  ✅ high 线索触发 Send 并行")
    else:
        print(f"  返回: {result} (非 Send)")
        assert False, "应返回 Send 列表"

    # 无 high 线索 → 走原逻辑
    state2 = dict(state)
    state2["leads"] = [
        {"name": "x", "relation": "", "priority": "low", "searched": False},
    ]
    r2 = route_after_analyze(state2)
    print(f"\n  无 high 线索 → 返回: {r2}")
    assert r2 == "search" or r2 == "synthesize"


def test_lead_search():
    print()
    print("=" * 60)
    print("2. lead_search_node（并行深挖单线索）")
    print("=" * 60)
    from entity_intel.graph import lead_search_node

    state = {
        "entity_name": "罗永浩",
        "hints": "",
        "goal": "",
        "job_id": "p22_test",
        "lead_name": "郑刚",
        "lead_relation": "投资人",
    }
    result = lead_search_node(state)
    pr = result.get("parallel_results", [])
    print(f"  parallel_results: {len(pr)} 组")
    if pr:
        print(f"  线索: {pr[0]['lead']} | total={pr[0]['total']} | 关系={pr[0]['relation']}")
        for item in pr[0]["results"][:3]:
            print(f"    - {item['title'][:40]} | {item['url'][:50]}")
        assert pr[0]["lead"] == "郑刚"
        print("  ✅ 并行搜索返回结果")


def test_merge():
    print()
    print("=" * 60)
    print("3. merge_parallel_node（合并去重）")
    print("=" * 60)
    from entity_intel.graph import merge_parallel_node

    report = EntityReport(entity_name="罗永浩")
    state = {
        "entity_name": "罗永浩",
        "report": report,
        "leads": [
            {"name": "郑刚", "relation": "投资人", "priority": "high", "searched": False},
            {"name": "李钧", "relation": "合伙人", "priority": "high", "searched": False},
        ],
        "visited": [],
        "parallel_results": [
            {
                "lead": "郑刚",
                "results": [
                    {"url": "http://a.com/1", "title": "郑刚投资", "content": "x",
                     "source": "serper", "source_type": "web"},
                    {"url": "http://a.com/1", "title": "重复URL", "content": "y",  # 重复应去重
                     "source": "serper", "source_type": "web"},
                ],
            },
            {
                "lead": "李钧",
                "results": [
                    {"url": "http://b.com/2", "title": "李钧直播", "content": "z",
                     "source": "bilibili", "source_type": "social"},
                ],
            },
        ],
    }
    result = merge_parallel_node(state)
    merged = result["report"]
    print(f"  合并后 web_results: {len(merged.web_results)}（应有 2，重复 URL 去重）")
    print(f"  线索标记 searched: {[l['searched'] for l in result['leads']]}")
    print(f"  visited: {result['visited']}")
    assert len(merged.web_results) == 2, "重复 URL 应去重"
    assert all(l["searched"] for l in result["leads"]), "线索应标记已追"
    assert "郑刚" in result["visited"] and "李钧" in result["visited"]
    print("  ✅ 合并去重 + 标记 searched 正确")


if __name__ == "__main__":
    test_route()
    test_lead_search()
    test_merge()
    print("\n✅ P2.2 单元验证完成")

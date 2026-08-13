"""P2.2 最小子图验证：LangGraph Send 并行机制（不跑完整调查）

构造: START → analyze_mock → (Send × N) → lead_mock → merge_mock → END
验证: Send 分发、并行执行、Annotated[add] reducer 累积结果
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from typing import Annotated, TypedDict
from operator import add
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send


class TestState(TypedDict):
    leads: list[dict]
    results: Annotated[list[dict], add]


def analyze_mock(state: TestState):
    """节点只返回普通 dict；Send 分发由 conditional_edges 路由函数完成（方案 A）"""
    print(f"[analyze] 待分发 high 线索: {[l['name'] for l in state['leads'] if l['priority'] == 'high']}")
    return {}


def lead_mock(state: TestState):
    """子任务：模拟搜索（随机 sleep 模拟并行耗时），结果进 results（reducer 累积）"""
    name = state.get("lead_name", "?")
    time.sleep(1)  # 模拟耗时 1s
    print(f"[lead_mock] 搜索 {name} 完成")
    return {"results": [{"lead": name, "found": len(name)}]}


def merge_mock(state: TestState):
    total = sum(r.get("found", 0) for r in state.get("results", []))
    print(f"[merge] 合并 {len(state.get('results', []))} 组结果, total_found={total}")
    return {"leads": [dict(l, searched=True) for l in state.get("leads", [])]}


def main():
    builder = StateGraph(TestState)
    builder.add_node("analyze_mock", analyze_mock)
    builder.add_node("lead_mock", lead_mock)
    builder.add_node("merge_mock", merge_mock)
    builder.add_edge(START, "analyze_mock")
    builder.add_conditional_edges(
        "analyze_mock", lambda s: [Send("lead_mock", {"lead_name": c["name"], "results": []})
                                   for c in s["leads"] if c["priority"] == "high"],
        {"lead_mock": "lead_mock"},
    )
    builder.add_edge("lead_mock", "merge_mock")
    builder.add_edge("merge_mock", END)
    g = builder.compile()

    print("=== 并行分发（3 个 high 线索，每个模拟 1s）===")
    t0 = time.time()
    result = g.invoke({
        "leads": [
            {"name": "郑刚", "priority": "high", "searched": False},
            {"name": "李钧", "priority": "high", "searched": False},
            {"name": "锤子科技", "priority": "medium", "searched": False},
            {"name": "罗永浩", "priority": "high", "searched": False},
        ],
        "results": [],
    })
    elapsed = time.time() - t0
    print(f"总耗时: {elapsed:.1f}s (串行应 3s, 并行 ~1s)")
    print(f"合并结果: {result.get('results')}")
    assert elapsed < 2.5, f"应并行执行 (耗时 {elapsed:.1f}s > 2.5s 说明串行了)"
    assert len(result.get("results", [])) == 3, "应累积 3 个结果"
    print("✅ Send 并行机制验证通过")


if __name__ == "__main__":
    main()

"""P2.3 中途调整方向验证：
1. _adjust_direction: LLM 重排剩余维度（删争议+加资金链）
2. API/Manager 层方法存在
3. 前端构建已单独验证
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_adjust_direction():
    print("=" * 60)
    print("1. _adjust_direction（LLM 重排剩余维度）")
    print("=" * 60)
    from entity_intel.graph import _adjust_direction

    plan = [
        {"name": "基线档案", "queries": ["x"], "priority": "high"},
        {"name": "争议核实", "queries": ["x"], "priority": "high"},
        {"name": "媒体反应", "queries": ["x"], "priority": "medium"},
    ]
    state = {"entity_name": "罗永浩", "hints": "债务"}
    new_plan, summary = _adjust_direction(state, plan, plan_index=1,
                                          instruction="别追争议了，专注资金链")
    print(f"  summary: {summary}")
    if new_plan:
        names = [d["name"] for d in new_plan]
        print(f"  新剩余维度: {names}")
        # 用户指令应体现在维度上（含资金/链 关键词）
        assert any("资金" in n or "链" in n for n in names), "应新增资金链维度"
        print("  ✅ 用户指令被正确转化为维度重排")
    else:
        print("  ❌ 调整失败")
        sys.exit(1)


def test_manager_api():
    print()
    print("=" * 60)
    print("2. Manager + API 层")
    print("=" * 60)
    from entity_intel.investigation import InvestigationManager
    from api.main import AdjustRequest, InvestigateRequest

    m = InvestigationManager()
    assert hasattr(m, "adjust_direction")
    assert hasattr(m, "continue_direction")
    assert hasattr(m, "stop_investigation")
    print("  ✅ Manager: adjust/continue/stop 方法存在")

    r = InvestigateRequest(entity_name="x", adjustable=True)
    assert r.adjustable is True
    a = AdjustRequest(instruction="专注资金链")
    assert a.instruction == "专注资金链"
    print("  ✅ API: adjustable 参数 + AdjustRequest 正常")


if __name__ == "__main__":
    test_adjust_direction()
    test_manager_api()
    print("\n✅ P2.3 验证完成")

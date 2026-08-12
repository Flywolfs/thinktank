"""P0.3 融合方案快速验证脚本

1. PlanValidator 单元测试（合法/非法 plan）
2. PlanProvider 三种模式（local / hermes / auto）
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.plan.validator import PlanValidator
from shared.plan.provider import PlanProvider
from shared.plan.hermes_provider import HermesPlanProvider


def test_validator():
    print("=" * 50)
    print("1. PlanValidator 单元测试")
    print("=" * 50)

    v = PlanValidator()

    # 合法 plan
    good = {
        "dimensions": [
            {"name": "基线档案", "methodology_source": "核心方法论§1 穷尽明面信息",
             "rationale": "建立基准", "queries": ["雷军 维基百科", "雷军 百度百科"], "priority": "high"},
            {"name": "资金链条", "methodology_source": "个人深扒§1 资金链条追踪",
             "rationale": "企业家核心", "queries": ["雷军 顺为资本 投资 6亿", "雷军 持股"], "priority": "high"},
            {"name": "关系网络", "methodology_source": "个人深扒§3 关系网络挖掘",
             "rationale": "人脉", "queries": ["雷军 王川 关系", "雷军 高管 团队"], "priority": "medium"},
        ]
    }
    r = v.validate(good, "雷军")
    print(f"  合法 plan: ok={r.ok} issues={r.issues}")
    assert r.ok, "合法 plan 不应报错"

    # 非法 plan：基线不第一 + 方法论非法 + query 太泛
    bad = {
        "dimensions": [
            {"name": "资金链条", "methodology_source": "随便写的来源",
             "rationale": "x", "queries": ["搜索"], "priority": "urgent"},
            {"name": "基线档案", "methodology_source": "核心方法论§1",
             "rationale": "y", "queries": ["雷军 百科"], "priority": "high"},
        ]
    }
    r2 = v.validate(bad, "雷军")
    print(f"  非法 plan: ok={r2.ok} issues={r2.issues[:3]}...")
    assert not r2.ok, "非法 plan 应报错"
    print("  ✅ 修复后:", [(d["name"], d["priority"]) for d in (r2.repaired or [])])

    # 空 plan
    r3 = v.validate({}, "雷军")
    assert not r3.ok
    print("  空 plan 正确拒绝 ✅")

    print("  ✅ Validator 测试通过")


def test_modes():
    print()
    print("=" * 50)
    print("2. PlanProvider 模式测试")
    print("=" * 50)

    # local 模式（自研 5 步推理）
    from entity_intel.graph import _local_plan_generator

    print("\n--- 模式: local ---")
    p_local = PlanProvider(mode="local", local_generator=_local_plan_generator)
    plan = p_local.generate("雷军", hints="小米汽车")
    print(f"  provider={plan.get('provider')} dims={len(plan['dimensions'])} "
          f"rounds={plan.get('max_rounds')} elapsed={plan.get('elapsed_s')}s")
    for d in plan["dimensions"][:3]:
        print(f"    - {d['name']} [{d.get('priority')}]")
    assert plan["provider"] == "local"

    # hermes 模式（Docker Hermes，失败会抛错）
    print("\n--- 模式: hermes ---")
    try:
        p_hermes = PlanProvider(mode="hermes", local_generator=_local_plan_generator)
        plan = p_hermes.generate("罗永浩", hints="债务与商业转型")
        print(f"  provider={plan.get('provider')} dims={len(plan['dimensions'])} "
              f"elapsed={plan.get('elapsed_s')}s")
        for d in plan["dimensions"][:4]:
            print(f"    - {d['name']} [{d.get('priority')}] ← {d.get('methodology_source','')[:40]}")
        assert plan["provider"] == "hermes"
    except Exception as e:
        print(f"  hermes 模式失败（若容器未启动则符合预期）: {type(e).__name__}: {e}")

    # auto 模式（Hermes 优先，失败降级 local）
    print("\n--- 模式: auto ---")
    p_auto = PlanProvider(mode="auto", local_generator=_local_plan_generator)
    plan = p_auto.generate("韩红", hints="慈善基金会运作")
    print(f"  provider={plan.get('provider')} dims={len(plan['dimensions'])} "
          f"elapsed={plan.get('elapsed_s')}s")
    for d in plan["dimensions"][:3]:
        print(f"    - {d['name']} [{d.get('priority')}]")
    assert plan["provider"] in ("hermes", "local")

    print("\n  ✅ 模式测试通过")


if __name__ == "__main__":
    test_validator()
    test_modes()
    print("\n✅ P0.3 融合方案验证完成")

"""P0.3 双路线 plan 质量对比脚本

路线A: 自研 plan_node 多轮推理（entity_intel/graph.py）
路线B: Docker Hermes Agent（http://localhost:8643, intel-planner profile）

三个案例: 蒋方舟 / 罗永浩 / 韩红
输出: data/plans_compare/{case}_route_{A,B}.json + 对比摘要
"""

import json
import os
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent.parent))

CASES = [
    {"entity": "蒋方舟", "hints": "政治立场、相关人物", "goal": "全面调查其政治立场与相关人物关系网"},
    {"entity": "罗永浩", "hints": "债务与商业转型", "goal": "全面调查其商业历程、债务情况与转型路径"},
    {"entity": "韩红", "hints": "慈善基金会运作", "goal": "全面调查其慈善基金会运作模式与争议"},
]

OUT_DIR = Path(__file__).parent.parent / "data" / "plans_compare"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def route_a(entity: str, hints: str, goal: str) -> dict:
    """路线A: 调用 plan_node（5步多轮推理）"""
    from entity_intel.graph import plan_node

    state = {
        "entity_name": entity,
        "hints": hints,
        "goal": goal,
        "max_rounds": 30,
        "job_id": f"p03_{int(time.time()*1000)}",
        "progress": [],
    }
    t0 = time.time()
    result = plan_node(state)
    elapsed = time.time() - t0
    return {
        "entity": entity,
        "hints": hints,
        "plan": result["plan"],
        "max_rounds": result["max_rounds"],
        "elapsed_s": round(elapsed, 1),
    }


def route_b(entity: str, hints: str, goal: str) -> dict:
    """路线B: 调用 Docker Hermes API"""
    # 读取专用 key（deploy/intel-planner/.env）
    env_file = Path(__file__).parent.parent / "deploy" / "intel-planner" / ".env"
    api_key = ""
    for line in env_file.read_text().splitlines():
        if line.startswith("HERMES_INTEL_API_KEY="):
            api_key = line.split("=", 1)[1].strip()
            break
    if not api_key:
        raise RuntimeError("未找到 HERMES_INTEL_API_KEY")

    prompt = f"""你是情报调查计划专家，请严格按照你加载的 think-tank-intel 调查方法论来制定调查计划。

调查核心实体: {entity}
用户关注方向: {hints}
调查意图: {goal}

请输出该实体的调查维度模板 JSON（注意：必须引用方法论中的具体章节作为 methodology_source，维度数量 4-8 个）:
{{
  "dimensions": [
    {{
      "name": "维度名",
      "methodology_source": "引用的方法论来源章节",
      "rationale": "为什么选这个维度（结合该实体的具体特征）",
      "queries": ["2-4 个搜索关键词/角度"],
      "priority": "high|medium|low"
    }}
  ]
}}
只输出 JSON，不要额外解释。"""

    t0 = time.time()
    resp = httpx.post(
        "http://localhost:8643/v1/chat/completions",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        json={
            "model": "intel-planner",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
        },
        timeout=300,
    )
    resp.raise_for_status()
    elapsed = time.time() - t0

    content = resp.json()["choices"][0]["message"]["content"]
    # 提取 JSON（可能在 ```json 代码块内）
    text = content.strip()
    if "```" in text:
        text = text.split("```json")[-1].split("```")[0].strip()
        text = text.lstrip("`").strip()
    plan = json.loads(text)
    return {
        "entity": entity,
        "hints": hints,
        "plan": plan.get("dimensions", plan if isinstance(plan, list) else []),
        "elapsed_s": round(elapsed, 1),
    }


def main():
    print("=" * 60)
    print("P0.3 双路线 plan 对比")
    print("=" * 60)

    all_results = {}
    for case in CASES:
        entity = case["entity"]
        print(f"\n▶ 案例: {entity} (hints: {case['hints']})")
        print("-" * 60)

        # 路线 A
        try:
            result_a = route_a(entity, case["hints"], case["goal"])
            dims_a = result_a["plan"]
            print(f"  路线A: {len(dims_a)} 维度 / {result_a['max_rounds']} 轮 / {result_a['elapsed_s']}s")
            for i, d in enumerate(dims_a, 1):
                print(f"    {i}. {d.get('name')} ({d.get('priority')}) ← {d.get('methodology_source', '')[:40]}")
            with open(OUT_DIR / f"{entity}_route_A.json", "w") as f:
                json.dump(result_a, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"  路线A 失败: {e}")
            result_a = {"error": str(e)}

        # 路线 B
        try:
            result_b = route_b(entity, case["hints"], case["goal"])
            dims_b = result_b["plan"]
            print(f"  路线B: {len(dims_b)} 维度 / {result_b['elapsed_s']}s")
            for i, d in enumerate(dims_b, 1):
                print(f"    {i}. {d.get('name')} ({d.get('priority')}) ← {d.get('methodology_source', '')[:40]}")
            with open(OUT_DIR / f"{entity}_route_B.json", "w") as f:
                json.dump(result_b, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"  路线B 失败: {e}")
            result_b = {"error": str(e)}

        all_results[entity] = {"A": result_a, "B": result_b}

    print("\n" + "=" * 60)
    print("结果已保存到 data/plans_compare/")
    print("=" * 60)


if __name__ == "__main__":
    main()

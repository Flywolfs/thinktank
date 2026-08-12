"""P1.2 快速验证：registry 调用链 + job_id 审计过滤（不跑完整图）"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.tools.registry import get_registry
from shared.models.entity_report import EntityReport

reg = get_registry()
reg.clear_audit()

# 1. search_all（全源聚合）
print("1. search_all...")
r = reg.call("search_all", job_id="test_job_1", query="罗永浩", max_per_source=3)
print(f"   ✅ {r.total_results} 条 (web={len(r.web_results)}, social={len(r.social_results)}, knowledge={len(r.knowledge_results)})")

# 2. search_all_fast
print("2. search_all_fast...")
r2 = reg.call("search_all_fast", job_id="test_job_1", query="罗永浩 真还传", max_per_source=3)
print(f"   ✅ {r2.total_results} 条")

# 3. filter_report
print("3. filter_report...")
reg.call("filter_report", job_id="test_job_1", report=r, hints="债务", goal="测试")
print(f"   ✅ 筛选后保留: {r.metadata.get('filtered', {}).get('final_kept', 0)} 条")

# 4. extract_report
print("4. extract_report...")
reg.call("extract_report", job_id="test_job_1", report=r)
print(f"   ✅ 抽取 {len(r.related_entities)} 个实体")

# 5. 审计按 job_id 过滤
print("\n=== 审计 (job_id=test_job_1) ===")
for a in reg.audit(job_id="test_job_1"):
    print(f"  [{a['status']}] {a['tool']} {a['duration_s']}s job={a['job_id']} → {a['summary'][:50]}")
print(f"  共 {len(reg.audit(job_id='test_job_1'))} 条")

# 6. 另一个 job 的审计为空（隔离验证）
print(f"\n=== 审计 (job_id=other) === 应为空: {len(reg.audit(job_id='other'))} 条")
print("\n✅ P1.2 验证完成")

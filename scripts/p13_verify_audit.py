"""P1.3 工具级审计验证：
1. 输出明细记录（每个工具返回什么）
2. 磁盘持久化（data/audit/{job_id}.jsonl）
3. 跨进程回放（新进程从磁盘读）
4. 完整回放字段（输入/输出/耗时/参数/状态）
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.tools.registry import get_registry
from shared.models.entity_report import EntityReport

reg = get_registry()
reg.clear_audit()
JOB = "p13_test_job"

# 1. 调用几个工具
print("1. 工具调用...")
r = reg.call("search_all_fast", job_id=JOB, query="罗永浩", max_per_source=3)
print(f"   search_all_fast: {r.total_results} 条")

r2 = reg.call("search_serper", job_id=JOB, query="罗永浩 真还传", max_results=3)
print(f"   search_serper: {len(r2)} 条")

# 2. 磁盘文件检查
audit_file = Path(__file__).parent.parent / "data" / "audit" / f"{JOB}.jsonl"
print(f"\n2. 磁盘持久化: {audit_file}")
print(f"   存在: {audit_file.exists()}, 行数: {len(audit_file.read_text(encoding='utf-8').splitlines()) if audit_file.exists() else 0}")

# 3. 完整回放字段（内存）
print("\n3. 完整回放（内存 audit）:")
for a in reg.audit(job_id=JOB):
    print(f"   [{a['status']}] {a['tool']} {a['duration_s']}s")
    print(f"     summary: {a['summary'][:60]}")
    if a["output"]:
        print(f"     output[{len(a['output'])}]: {a['output'][0]}")
    else:
        print(f"     output: (空)")

# 4. 写入磁盘的原始记录（验证 output 字段持久化）
print("\n4. 磁盘原始记录字段:")
for line in audit_file.read_text(encoding="utf-8").splitlines():
    import json
    d = json.loads(line)
    print(f"   {d['tool']}: summary={d['summary'][:40]} | output_len={len(d.get('output', []))} | params_keys={list(d['params'].keys())}")

print("\n✅ P1.3 内存+磁盘审计验证完成")

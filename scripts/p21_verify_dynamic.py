"""P2.1 动态工具创建端到端验证：
1. Hermes 生成爬虫代码（简单示例：Wikipedia 搜索）
2. 代码保存到 tools/dynamic/（pending）
3. approve: importlib 加载 + health_check + 注册进 registry
4. 注册后可调用（dynamic_xxx 工具）
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.tools.dynamic import get_dynamic_manager
from shared.tools.registry import get_registry

mgr = get_dynamic_manager()
reg = get_registry()

print("=" * 60)
print("P2.1 动态工具创建验证")
print("=" * 60)

# 1. 生成（调 Hermes）
print("\n1. Hermes 生成爬虫代码...")
try:
    tool = mgr.generate(
        "抓取维基百科搜索结果的摘要，返回前几篇条目的标题和链接",
        name="wiki_demo",
    )
    print(f"   生成: {tool.name} (status={tool.status})")
    print(f"   代码长度: {len(tool.code)} 字符")
    print(f"   代码前 100 字: {tool.code[:100]!r}")
except Exception as e:
    print(f"   ❌ 生成失败: {e}")
    sys.exit(1)

# 2. 检查保存
code_path = Path(__file__).parent.parent / "tools" / "dynamic" / "wiki_demo.py"
meta_path = Path(__file__).parent.parent / "tools" / "dynamic" / "wiki_demo.meta.json"
print(f"\n2. 文件保存:")
print(f"   {code_path} 存在: {code_path.exists()}")
print(f"   {meta_path} 存在: {meta_path.exists()}")
assert code_path.exists() and meta_path.exists()

# 3. 列表
tools = mgr.list()
print(f"\n3. 动态工具列表: {len(tools)} 个")
for t in tools:
    print(f"   {t['name']} [{t['status']}] {t['description'][:50]}")

# 4. approve（加载 + health_check + 注册）
print(f"\n4. 审批通过 → 注册进 registry...")
try:
    tool = mgr.approve("wiki_demo", registry=reg)
    print(f"   状态: {tool.status}")
    assert tool.status == "approved"
except Exception as e:
    print(f"   ❌ 审批失败: {e}")
    sys.exit(1)

# 5. 检查注册
print(f"\n5. registry 检查:")
print(f"   dynamic_wiki_demo 在 registry: {'dynamic_wiki_demo' in reg}")
spec = reg.get_spec("dynamic_wiki_demo")
if spec:
    print(f"   spec: name={spec.name} category={spec.category} provider={spec.provider}")
    print(f"   description: {spec.description[:50]}")

# 6. 调用动态工具（会真正执行爬虫逻辑，wiki_demo 应该调 wikipedia API）
print(f"\n6. 调用 dynamic_wiki_demo...")
try:
    results = reg.call("dynamic_wiki_demo", job_id="p21_test", query="罗永浩", max_results=3)
    print(f"   返回 {len(results)} 条")
    for r in results[:3]:
        print(f"   - {r.title[:50]} | {r.url[:60]}")
except Exception as e:
    print(f"   ❌ 调用失败: {e}")

print("\n✅ P2.1 端到端验证完成")

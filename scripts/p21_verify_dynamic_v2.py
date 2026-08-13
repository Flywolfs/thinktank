"""P2.1 v2 端到端验证：Hermes 直接写文件模式

1. generate(): 清旧文件 → Hermes 写 /opt/tools_dynamic/{name}.py → 轮询读
2. 文件内容应为纯代码（无 markdown 标记）
3. approve: importlib 加载 + health_check + 注册
4. 调用动态工具
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.tools.dynamic import get_dynamic_manager
from shared.tools.registry import get_registry

mgr = get_dynamic_manager()
reg = get_registry()

print("=" * 60)
print("P2.1 v2 文件写入模式验证")
print("=" * 60)

# 0. 清理旧测试工具
for old in ("wiki_demo",):
    p = Path(__file__).parent.parent / "tools" / "dynamic" / f"{old}.py"
    p.unlink(missing_ok=True)
    m = Path(__file__).parent.parent / "tools" / "dynamic" / f"{old}.meta.json"
    m.unlink(missing_ok=True)

# 1. 生成（Hermes 写文件 + 轮询）
print("\n1. generate() — Hermes 写文件 + 框架轮询...")
try:
    tool = mgr.generate(
        "抓取维基百科搜索结果的摘要，返回前几篇条目的标题和链接",
        name="wiki_demo",
    )
    print(f"   生成: {tool.name} (status={tool.status})")
    print(f"   代码长度: {len(tool.code)} 字符")
    print(f"   代码前 80 字: {tool.code[:80]!r}")
except Exception as e:
    print(f"   ❌ 生成失败: {type(e).__name__}: {e}")
    sys.exit(1)

# 2. 纯代码检查（无 markdown 标记）
print("\n2. 纯代码检查（无 ``` / python 前缀）:")
has_md = "```" in tool.code or tool.code.startswith("python\n")
print(f"   含 markdown 标记: {has_md}")
starts = tool.code.lstrip()
ok_start = starts.startswith(("import", "class", '"""', "from"))
print(f"   以 import/class/\"\"\" 开头: {ok_start}")
if has_md:
    print("   ❌ 仍有 markdown 残留")
    sys.exit(1)
print("   ✅ 纯代码，无后处理痕迹")

# 3. 审批 + 注册
print("\n3. approve() → importlib 加载 + health_check + 注册...")
try:
    tool = mgr.approve("wiki_demo", registry=reg)
    print(f"   状态: {tool.status}")
except Exception as e:
    print(f"   ❌ 审批失败: {e}")
    sys.exit(1)

# 4. 调用
print("\n4. 调用 dynamic_wiki_demo...")
try:
    results = reg.call("dynamic_wiki_demo", job_id="p21v2_test", query="罗永浩", max_results=3)
    print(f"   返回 {len(results)} 条")
    for r in results[:3]:
        print(f"   - {r.title[:45]} | {r.url[:60]}")
except Exception as e:
    print(f"   ❌ 调用失败: {e}")
    sys.exit(1)

print("\n✅ P2.1 v2 文件写入模式验证完成")

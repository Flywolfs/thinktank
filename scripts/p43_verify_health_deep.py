"""深度检测验证: 实际搜索验证登录态（慢）"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.crawlers.health import get_health_checker

checker = get_health_checker()
print("=== 深度检测（实际搜索，各源 ~10-90s）===")
for name in ("zhihu", "xiaohongshu", "weibo"):
    st = checker.deep_search_check(name, query="雷军", max_results=3)
    icon = {"ok": "OK", "error": "ERR", "warning": "WARN"}.get(st.status, "?")
    print(f"[{icon}] {st.name:<12} {st.message} ({st.duration_ms}ms)")

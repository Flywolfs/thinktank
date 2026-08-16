"""数据源健康检测器快速验证"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.crawlers.health import get_health_checker

checker = get_health_checker()
results = checker.check_all()
print("=== 数据源健康状态（快速检查）===")
for r in results:
    icon = {"ok": "OK", "error": "ERR", "warning": "WARN", "disabled": "OFF"}.get(r["status"], "?")
    print(f"[{icon}] {r['name']:<12} {r['message'][:70]} ({r['duration_ms']}ms)")
    for c in r["checks"]:
        print(f"      - {c}")

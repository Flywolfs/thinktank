"""统一日志工具 — 调查过程结构化日志 + 搜索存档

所有日志写入 data/logs/ 目录，JSONL 格式（每行一个事件，可用 jq 查询）。
搜索原始结果统一存档到 data/raw/search/。

用法:
    logger = InvestigationLogger(job_id="inv_xxx")
    logger.node_start("search", {"query": "雷军"})
    logger.node_end("search", {"results": 37}, duration_ms=28000)
    logger.decision("analyze_leads", "进入下一维度", {"dimension": "历史回溯"})
"""

import json
import time
from datetime import datetime
from pathlib import Path

from shared.crawlers.base import SearchResult

# 项目根目录 (shared/utils/logger.py → shared/ → 项目根)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
LOGS_DIR = _PROJECT_ROOT / "data" / "logs"
RAW_DIR = _PROJECT_ROOT / "data" / "raw"


def _safe_json(obj) -> str:
    """把任意对象安全转 JSON（处理 datetime/Path 等）"""
    def _default(o):
        if isinstance(o, datetime):
            return o.isoformat()
        if isinstance(o, Path):
            return str(o)
        if hasattr(o, "to_dict"):
            return o.to_dict()
        return str(o)
    return json.dumps(obj, ensure_ascii=False, default=_default)


class InvestigationLogger:
    """调查过程的结构化日志（JSONL）"""

    def __init__(self, job_id: str = "unknown", thread_id: str = ""):
        self.job_id = job_id
        self.thread_id = thread_id
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        # 文件名用 job_id（唯一），同一调查的所有节点写同一个文件
        safe_job = (job_id or "unknown").replace("/", "_")[:40]
        self.log_file = LOGS_DIR / f"investigation_{safe_job}.jsonl"

    def _write(self, event: dict):
        record = {
            "ts": time.time(),
            "ts_iso": datetime.now().isoformat(),
            "job_id": self.job_id,
            "thread_id": self.thread_id,
            **event,
        }
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(_safe_json(record) + "\n")

    # ── 常用事件 ───────────────────────────────────────

    def node_start(self, node: str, detail: dict | None = None):
        """节点开始"""
        self._write({"event": "node_start", "node": node, "detail": detail or {}})

    def node_end(self, node: str, detail: dict | None = None, duration_ms: float = 0):
        """节点结束（含耗时）"""
        self._write({
            "event": "node_end", "node": node,
            "detail": detail or {}, "duration_ms": round(duration_ms, 1),
        })

    def decision(self, node: str, decision: str, reason: str = "", detail: dict | None = None):
        """关键决策点（LLM 判断、维度轮转等）"""
        self._write({
            "event": "decision", "node": node, "decision": decision,
            "reason": reason, "detail": detail or {},
        })

    def tool_call(self, tool: str, args: dict, result_summary: str = "", error: str = "", duration_ms: float = 0):
        """工具调用记录"""
        self._write({
            "event": "tool_call", "tool": tool, "args": args,
            "result_summary": result_summary[:500], "error": error,
            "duration_ms": round(duration_ms, 1),
        })

    def error(self, node: str, error: str):
        """错误记录"""
        self._write({"event": "error", "node": node, "error": error[:500]})

    def info(self, node: str, message: str, detail: dict | None = None):
        """一般信息"""
        self._write({"event": "info", "node": node, "message": message, "detail": detail or {}})


# ── 搜索原始结果统一存档 ───────────────────────────────

def archive_search_results(results: list[SearchResult], query: str, source: str = "search"):
    """
    把一次搜索的所有源结果统一存档到 data/raw/search/{ts}_{query}.jsonl。
    这样 serper/bilibili/wikipedia 等不产生平台 jsonl 的源，原始结果也不会丢失。

    Args:
        results: SearchResult 列表
        query: 搜索关键词
        source: 存档子目录（search / search_fast）
    """
    if not results:
        return None
    try:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_q = "".join(c if c not in '\\/:*?"<>|' else "_" for c in query)[:40]
        out_dir = RAW_DIR / source
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / f"{ts}_{safe_q}.jsonl"

        lines = []
        for r in results:
            lines.append(_safe_json({
                "source": r.source,
                "source_type": r.source_type,
                "url": r.url,
                "title": r.title,
                "content": r.content[:2000],
                "author": r.author,
                "published_at": r.published_at.isoformat() if r.published_at else None,
                "metadata": r.metadata,
                "query": query,
            }))
        out_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return out_file
    except Exception as e:
        print(f"[logger] 存档失败: {e}")
        return None

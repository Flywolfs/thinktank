"""统一日志工具 — 调查过程结构化日志 + 搜索存档

日志分层:
    info  — 操作级：节点生命周期、决策、工具调用概要
    debug — 代码级：每个数据源的请求/响应/耗时/错误、LLM 调用输入输出、关键变量

所有日志写入 data/logs/ 目录，JSONL 格式。
搜索原始结果统一存档到 data/raw/search/。

用法:
    # 直接使用
    logger = InvestigationLogger(job_id="inv_xxx")
    logger.node_start("search", {...})
    logger.provider_result("serper", "雷军", count=10, duration_ms=1200)
    logger.llm_call("deepseek-chat", prompt_chars=2345, response="{...}")

    # 全局上下文（底层组件用）
    set_current_logger(logger)     # graph 启动调查时设置
    get_current_logger().provider_result(...)   # searcher/llm client 里调用
"""

import json
import time
import threading
from datetime import datetime
from pathlib import Path

from shared.crawlers.base import SearchResult

# 项目根目录 (shared/utils/logger.py → shared/ → 项目根)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
LOGS_DIR = _PROJECT_ROOT / "data" / "logs"
RAW_DIR = _PROJECT_ROOT / "data" / "raw"

# 全局 logger 上下文（thread-local，底层组件通过 get_current_logger 获取）
_local = threading.local()


def set_current_logger(logger: "InvestigationLogger | None"):
    """设置当前线程的调查 logger（graph 启动调查时调用）"""
    _local.logger = logger


def get_current_logger() -> "InvestigationLogger":
    """获取当前线程的调查 logger（无则返回 no-op logger）"""
    logger = getattr(_local, "logger", None)
    if logger is None:
        logger = InvestigationLogger(job_id="noop")
        logger.enabled = False
        _local.logger = logger
    return logger


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
    """调查过程的结构化日志（JSONL），info + debug 双层"""

    def __init__(self, job_id: str = "unknown", thread_id: str = ""):
        self.job_id = job_id
        self.thread_id = thread_id
        self.enabled = True
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        # 文件名用 job_id（唯一），同一调查的所有节点写同一个文件
        safe_job = (job_id or "unknown").replace("/", "_")[:40]
        self.log_file = LOGS_DIR / f"investigation_{safe_job}.jsonl"

    @staticmethod
    def _caller_location(stack_level: int = 0) -> dict:
        """捕获调用者代码位置（文件:行号:函数名）。

        stack_level=0 时返回 InvestigationLogger._write 自己的位置，
        所以调用方需要传 1（Logger 方法）或 2（真正业务代码）。
        为准确起见用 stack 深度计算：__caller_location 在 _write 内调用，
        调用链为 _write <- logger.xxx() <- 业务代码。
        """
        import inspect
        try:
            frame = inspect.currentframe()
            # frame: _caller_location → _write → logger.xxx → 业务代码
            # 需要上溯 3 层到达业务代码
            for _ in range(3 + stack_level):
                if frame is None or frame.f_back is None:
                    break
                frame = frame.f_back
            filename = frame.f_code.co_filename if frame else ""
            lineno = frame.f_lineno if frame else 0
            funcname = frame.f_code.co_name if frame else ""
            # 只保留相对路径（项目内）
            project_root = str(_PROJECT_ROOT)
            if filename.startswith(project_root):
                filename = filename[len(project_root) + 1:]
            return {"file": filename, "line": lineno, "func": funcname}
        except Exception:
            return {}

    def _write(self, event: str, level: str, node: str, **extra):
        if not self.enabled:
            return
        record = {
            "ts": time.time(),
            "ts_iso": datetime.now().isoformat(),
            "job_id": self.job_id,
            "event": event,
            "level": level,
            "node": node,
            "code": self._caller_location(stack_level=0),
            **extra,
        }
        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(_safe_json(record) + "\n")
        except Exception:
            pass  # 日志失败不影响主流程

    # ── info 级：操作生命周期 ───────────────────────────

    def node_start(self, node: str, detail: dict | None = None):
        self._write("node_start", "info", node, detail=detail or {})

    def node_end(self, node: str, detail: dict | None = None, duration_ms: float = 0):
        self._write("node_end", "info", node,
                    detail=detail or {}, duration_ms=round(duration_ms, 1))

    def decision(self, node: str, decision: str, reason: str = "", detail: dict | None = None):
        self._write("decision", "info", node,
                    decision=decision, reason=reason, detail=detail or {})

    def tool_call(self, tool: str, args: dict, result_summary: str = "", error: str = "", duration_ms: float = 0):
        self._write("tool_call", "info", "tool",
                    tool=tool, args=args, result_summary=result_summary[:500],
                    error=error, duration_ms=round(duration_ms, 1))

    def error(self, node: str, error: str):
        self._write("error", "info", node, error=error[:500])

    def info(self, node: str, message: str, detail: dict | None = None):
        self._write("info", "info", node, message=message, detail=detail or {})

    # ── debug 级：代码级细节 ────────────────────────────

    def provider_result(self, provider: str, query: str, count: int,
                        duration_ms: float = 0, error: str = "", **extra):
        """单个数据源的搜索结果（哪个源、返回多少、耗时、是否失败）"""
        self._write("provider_result", "debug", "search",
                    provider=provider, query=query, count=count,
                    duration_ms=round(duration_ms, 1), error=error, **extra)

    def provider_call(self, provider: str, query: str, **args):
        """数据源调用开始（含请求参数）"""
        self._write("provider_call", "debug", "search",
                    provider=provider, query=query, args=args)

    def llm_call(self, model: str, prompt_chars: int, response: str = "",
                 error: str = "", duration_ms: float = 0, **extra):
        """LLM 调用记录（模型、prompt 长度、响应摘要/全文、耗时）"""
        self._write("llm_call", "debug", "llm",
                    model=model, prompt_chars=prompt_chars,
                    response=response[:2000], error=error[:500],
                    duration_ms=round(duration_ms, 1), **extra)

    def variable(self, node: str, name: str, value):
        """关键变量/中间结果记录"""
        self._write("variable", "debug", node, name=name,
                    value=_safe_json(value)[:2000])

    def extract_result(self, entity_count: int, entities: list[str] | None = None, error: str = ""):
        """实体抽取结果"""
        self._write("extract_result", "debug", "extract",
                    entity_count=entity_count, entities=entities or [],
                    error=error[:500])

    def asr_result(self, bvid: str, chars: int, error: str = ""):
        """B站 ASR 转录结果"""
        self._write("asr_result", "debug", "deep_audio",
                    bvid=bvid, chars=chars, error=error[:500])


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

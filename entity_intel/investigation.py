"""调查任务管理 — HITL 状态机（基于 LangGraph）

使用 LangGraph 图执行调查（entity_intel/graph.py），
review 节点 interrupt() 暂停等用户审阅，Command(resume) 恢复。

状态: pending → running → review(等用户审阅) → approved(建图谱) / rejected(丢弃)
"""

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from shared.models.analysis_report import AnalysisReport

STORAGE_DIR = Path(__file__).parent.parent / "data" / "investigations"


@dataclass
class InvestigationJob:
    """一次调查任务的完整状态"""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    entity_name: str = ""
    hints: str = ""
    goal: str = ""
    status: str = "pending"           # pending/running/review/approved/rejected/error
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    progress: list[dict] = field(default_factory=list)  # [{phase, detail, ts}]
    error: str = ""
    report: dict = field(default_factory=dict)          # AnalysisReport.to_dict()
    report_markdown: str = ""                           # AnalysisReport.to_markdown()
    graph_built: bool = False
    thread_id: str = ""                 # LangGraph checkpoint thread_id（resume 用）

    def add_progress(self, phase: str, detail: str = ""):
        self.progress.append({"phase": phase, "detail": detail, "ts": time.time()})
        self.updated_at = time.time()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "entity_name": self.entity_name,
            "hints": self.hints,
            "goal": self.goal,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "progress": self.progress,
            "error": self.error,
            "report": self.report,
            "report_markdown": self.report_markdown,
            "graph_built": self.graph_built,
            "thread_id": self.thread_id,
        }


class InvestigationStore:
    """调查任务的持久化存储（JSON 文件）"""

    def __init__(self, storage_dir: Path | None = None):
        self.dir = storage_dir or STORAGE_DIR
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, job_id: str) -> Path:
        return self.dir / f"{job_id}.json"

    def save(self, job: InvestigationJob):
        self._path(job.id).write_text(
            json.dumps(job.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def load(self, job_id: str) -> InvestigationJob | None:
        p = self._path(job_id)
        if not p.exists():
            return None
        data = json.loads(p.read_text(encoding="utf-8"))
        job = InvestigationJob(**{k: v for k, v in data.items() if k != "progress"})
        job.progress = data.get("progress", [])
        return job

    def list_jobs(self, limit: int = 20) -> list[dict]:
        jobs = []
        for p in sorted(self.dir.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True)[:limit]:
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                jobs.append({
                    "id": data["id"],
                    "entity_name": data["entity_name"],
                    "status": data["status"],
                    "created_at": data["created_at"],
                    "report_ready": bool(data.get("report")),
                })
            except Exception:
                continue
        return jobs


class InvestigationManager:
    """调查任务管理器 — 基于 LangGraph 的 HITL 流程"""

    def __init__(self, store: InvestigationStore | None = None):
        self.store = store or InvestigationStore()

    # ── 用户操作 ───────────────────────────────────────

    def start(self, entity_name: str, hints: str = "", goal: str = "") -> InvestigationJob:
        """启动一次调查，执行到 review 暂停点"""
        job = InvestigationJob(
            entity_name=entity_name,
            hints=hints,
            goal=goal,
            status="running",
        )
        job.add_progress("start", f"开始调查实体: {entity_name}")
        self.store.save(job)

        try:
            from entity_intel.graph import run_investigation

            result, thread_id = run_investigation(entity_name, hints=hints, goal=goal)

            # 从图状态同步结果
            job.thread_id = thread_id  # 保存 resume 用的 thread_id
            job.progress = result.get("progress", job.progress)
            analysis: AnalysisReport = result.get("analysis")
            if analysis and analysis.entity_summary:
                job.report = analysis.to_dict()
                job.report_markdown = analysis.to_markdown()

            job.status = "review"
            job.add_progress("review", "报告已生成，等待用户审阅决定是否构建知识图谱")

        except Exception as e:
            job.status = "error"
            job.error = str(e)
            job.add_progress("error", f"调查失败: {e}")

        self.store.save(job)
        return job

    def approve(self, job_id: str) -> InvestigationJob:
        """用户批准，通过 Command(resume) 恢复图执行并构建图谱"""
        job = self._require(job_id)
        if job.status != "review":
            job.error = f"当前状态 {job.status} 不可批准（需 review）"
            self.store.save(job)
            return job

        try:
            from entity_intel.graph import resume_investigation

            result = resume_investigation(job.thread_id, action="approve")
            job.progress = result.get("progress", job.progress)
            job.graph_built = True
            job.status = "approved"
            # 最后一步进度应该是 graph_done
            if job.progress and job.progress[-1]["phase"] == "graph_done":
                job.add_progress("approved", "调查完成")

        except Exception as e:
            job.status = "error"
            job.error = f"图谱构建失败: {e}"
            job.add_progress("error", f"图谱构建失败: {e}")

        self.store.save(job)
        return job

    def reject(self, job_id: str) -> InvestigationJob:
        """用户拒绝/丢弃"""
        job = self._require(job_id)
        try:
            from entity_intel.graph import resume_investigation
            resume_investigation(job.thread_id, action="reject")
        except Exception:
            pass  # reject 失败不阻塞状态更新
        job.status = "rejected"
        job.add_progress("reject", "用户丢弃本次调查")
        self.store.save(job)
        return job

    def get(self, job_id: str) -> InvestigationJob | None:
        return self.store.load(job_id)

    def list(self, limit: int = 20) -> list[dict]:
        return self.store.list_jobs(limit)

    # ── 工具 ───────────────────────────────────────────

    def _require(self, job_id: str) -> InvestigationJob:
        job = self.store.load(job_id)
        if not job:
            raise FileNotFoundError(f"调查任务不存在: {job_id}")
        return job


# ==================== 自检 ====================
if __name__ == "__main__":
    print("InvestigationManager (LangGraph) 自检")
    print("=" * 40)

    mgr = InvestigationManager()
    job = mgr.start("雷军", hints="小米汽车")
    print(f"任务: {job.id} | 状态: {job.status}")
    print(f"进度: {len(job.progress)} 步")
    for p in job.progress:
        print(f"  [{p['phase']}] {p['detail']}")

    if job.status == "review":
        print(f"\n✅ HITL: 报告已生成，等用户审阅")
        job2 = mgr.approve(job.id)
        print(f"批准后: {job2.status} | 图谱构建: {job2.graph_built}")
        if job2.error:
            print(f"❌ {job2.error}")
        else:
            print(f"最后进度: {job2.progress[-1]['detail']}")

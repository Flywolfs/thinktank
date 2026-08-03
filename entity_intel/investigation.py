"""调查任务管理 — HITL 状态机

流程: 启动调查 → running → review(等用户审阅) → approved(建图谱) / rejected(丢弃)

状态:
    pending   - 已创建，等待执行
    running   - 调查执行中
    review    - 报告已生成，等待用户审阅（HITL 关键点）
    approved  - 用户批准，构建知识图谱
    rejected  - 用户拒绝/丢弃
    error     - 执行失败
"""

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from shared.models.analysis_report import AnalysisReport
from shared.models.entity_report import EntityReport

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
        }


class InvestigationStore:
    """调查任务的持久化存储（JSON 文件，MVP 够用）"""

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
    """调查任务管理器 — 编排完整 HITL 流程"""

    def __init__(self, store: InvestigationStore | None = None):
        self.store = store or InvestigationStore()
        self._searcher = None  # 惰性导入，避免循环依赖

    def _get_searcher(self):
        if self._searcher is None:
            from entity_intel.searcher import EntitySearcher
            self._searcher = EntitySearcher()
        return self._searcher

    # ── 用户操作 ───────────────────────────────────────

    def start(self, entity_name: str, hints: str = "", goal: str = "") -> InvestigationJob:
        """启动一次调查"""
        job = InvestigationJob(
            entity_name=entity_name,
            hints=hints,
            goal=goal,
            status="running",
        )
        job.add_progress("start", f"开始调查实体: {entity_name}")
        self.store.save(job)

        try:
            searcher = self._get_searcher()

            job.add_progress("search", "多源并行搜索 (Wikipedia/Google/B站/企业)")
            report = searcher.search(entity_name, max_per_source=10)
            job.add_progress("search_done", f"搜索完成: {report.total_results} 条")

            job.add_progress("filter", "相关性筛选（两阶段）")
            searcher.filter_results(report, hints=hints, goal=goal)
            job.add_progress("filter_done", f"筛选保留 {report.metadata.get('filtered', {}).get('final_kept', 0)} 条")

            job.add_progress("extract", "LLM 实体抽取")
            searcher.extract_entities(report)
            job.add_progress("extract_done", f"抽取 {len(report.related_entities)} 个相关实体")

            job.add_progress("synthesize", "信息整合推理，生成逻辑链路报告")
            analysis = searcher.synthesize(report, hints=hints, goal=goal)
            job.report = analysis.to_dict()
            job.report_markdown = analysis.to_markdown()
            job.add_progress("synthesize_done", f"报告生成: {len(analysis.logical_chains)} 条逻辑链路")

            # HITL 关键点：进入 review 状态，等用户审阅
            job.status = "review"
            job.add_progress("review", "报告已生成，等待用户审阅决定是否构建知识图谱")

        except Exception as e:
            job.status = "error"
            job.error = str(e)
            job.add_progress("error", f"调查失败: {e}")

        self.store.save(job)
        return job

    def approve(self, job_id: str) -> InvestigationJob:
        """用户批准，构建知识图谱"""
        job = self._require(job_id)
        if job.status != "review":
            job.error = f"当前状态 {job.status} 不可批准（需 review）"
            self.store.save(job)
            return job

        try:
            job.add_progress("graph", "构建知识图谱 (Neo4j)")
            from shared.storage.neo4j_client import Neo4jClient

            client = Neo4jClient()
            client.ensure_indexes()

            # 1. 核心实体节点
            client.merge_entity(
                job.entity_name,
                entity_type="person",
                summary=job.report.get("entity_summary", "")[:500],
                source="investigation",
            )

            # 2. 建议实体节点
            for e in job.report.get("suggested_entities", []):
                client.merge_entity(
                    e.get("name", ""),
                    entity_type=e.get("type", ""),
                    source="investigation",
                )

            # 3. 建议关系（from → to）
            rel_count = 0
            for r in job.report.get("suggested_relations", []):
                frm, to, rel = r.get("from", ""), r.get("to", ""), r.get("relation", "")
                if frm and to and rel:
                    client.merge_relation(frm, to, rel, source="investigation")
                    rel_count += 1

            job.graph_built = True
            job.status = "approved"
            job.add_progress(
                "graph_done",
                f"图谱构建完成: {len(job.report.get('suggested_entities', []))} 实体, {rel_count} 关系",
            )

        except Exception as e:
            job.status = "error"
            job.error = f"图谱构建失败: {e}"
            job.add_progress("error", f"图谱构建失败: {e}")

        self.store.save(job)
        return job

    def reject(self, job_id: str) -> InvestigationJob:
        """用户拒绝/丢弃报告"""
        job = self._require(job_id)
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
    import sys

    print("InvestigationManager 自检")
    print("=" * 40)

    mgr = InvestigationManager()

    # 用一个小实体快速验证（不走完整调查，只验证状态机）
    test_id = "test123"
    job = mgr.start("雷军", hints="小米汽车")
    print(f"任务: {job.id} | 状态: {job.status}")
    print(f"进度: {len(job.progress)} 步")
    for p in job.progress:
        print(f"  [{p['phase']}] {p['detail']}")

    if job.status == "review":
        print(f"\n✅ HITL: 报告已生成，等用户审阅")
        print(f"报告含: {len(job.report.get('logical_chains', []))} 条链路")

        # 模拟用户批准
        job2 = mgr.approve(job.id)
        print(f"\n批准后状态: {job2.status} | 图谱构建: {job2.graph_built}")
    elif job.status == "error":
        print(f"\n❌ 调查失败: {job.error}")

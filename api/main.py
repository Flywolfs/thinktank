"""智库情报系统 — FastAPI 后端入口

API 一览:
    POST /api/investigate       启动调查（返回 job_id）
    GET  /api/jobs              调查任务列表
    GET  /api/jobs/{id}         调查进度/状态
    GET  /api/jobs/{id}/report  报告详情
    POST /api/jobs/{id}/approve 用户批准 → 构建知识图谱
    POST /api/jobs/{id}/reject  用户拒绝
    GET  /api/graph/{name}      查询知识图谱实体

启动: uvicorn api.main:app --reload --port 8899
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from entity_intel.investigation import InvestigationManager
from shared.storage.neo4j_client import Neo4jClient

app = FastAPI(title="智库情报系统", version="0.1.0")

# CORS: 允许前端 (Vite dev server 5173)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_mgr = InvestigationManager()


# ── 请求模型 ──────────────────────────────────────────

class InvestigateRequest(BaseModel):
    entity_name: str
    hints: str = ""
    goal: str = ""
    max_rounds: int = 30


# ── 调查流程 API ──────────────────────────────────────

@app.post("/api/investigate")
def investigate(req: InvestigateRequest):
    """启动一次实体调查（多轮深挖），返回 job_id"""
    if not req.entity_name.strip():
        raise HTTPException(400, "entity_name 不能为空")

    rounds = max(1, req.max_rounds)  # 由用户设定，不硬限制上限
    job = _mgr.start(
        req.entity_name.strip(),
        hints=req.hints.strip(),
        goal=req.goal.strip(),
        max_rounds=rounds,
    )
    return {"job_id": job.id, "status": job.status}


@app.get("/api/jobs")
def list_jobs(limit: int = 20):
    """调查任务列表"""
    return {"jobs": _mgr.list(limit)}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    """调查进度/状态"""
    job = _mgr.get(job_id)
    if not job:
        raise HTTPException(404, f"调查任务不存在: {job_id}")
    return job.to_dict()


@app.get("/api/jobs/{job_id}/report")
def get_report(job_id: str):
    """报告详情（含 Markdown）"""
    job = _mgr.get(job_id)
    if not job:
        raise HTTPException(404, f"调查任务不存在: {job_id}")
    if not job.report:
        raise HTTPException(400, "报告尚未生成")
    return {
        "job_id": job.id,
        "status": job.status,
        "report": job.report,
        "report_markdown": job.report_markdown,
    }


@app.post("/api/jobs/{job_id}/approve")
def approve_job(job_id: str):
    """用户批准 → 构建知识图谱"""
    try:
        job = _mgr.approve(job_id)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    return job.to_dict()


@app.post("/api/jobs/{job_id}/reject")
def reject_job(job_id: str):
    """用户拒绝/丢弃"""
    try:
        job = _mgr.reject(job_id)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    return job.to_dict()


# ── 知识图谱 API ──────────────────────────────────────

@app.get("/api/graph/{name}")
def get_graph_entity(name: str):
    """查询知识图谱中的实体及其关系"""
    try:
        client = Neo4jClient()
        entity = client.get_entity(name)
        if not entity:
            return {"found": False, "name": name}
        return {"found": True, "entity": entity}
    except Exception as e:
        raise HTTPException(500, f"图谱查询失败: {e}")


@app.get("/api/graph/subgraph/{name}")
def get_subgraph(name: str, depth: int = 1):
    """查询以某实体为中心的子图"""
    try:
        client = Neo4jClient()
        subgraph = client.get_subgraph(name, depth=depth)
        return {"name": name, "depth": depth, "entities": subgraph}
    except Exception as e:
        raise HTTPException(500, f"图谱查询失败: {e}")


@app.get("/health")
def health():
    return {"status": "ok"}

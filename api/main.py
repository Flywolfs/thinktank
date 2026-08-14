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

import json

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
    plan_provider: str = ""     # auto/hermes/local（空=config 默认 auto）
    adjustable: bool = False    # P2.3: 每轮可中途调整方向


# ── 调查流程 API ──────────────────────────────────────

@app.post("/api/investigate")
def investigate(req: InvestigateRequest):
    """启动一次实体调查（多轮深挖），返回 job_id"""
    if not req.entity_name.strip():
        raise HTTPException(400, "entity_name 不能为空")

    provider = (req.plan_provider or "").strip().lower()
    if provider and provider not in ("auto", "hermes", "local"):
        raise HTTPException(400, f"plan_provider 非法: {provider}（可选 auto/hermes/local）")

    rounds = max(1, req.max_rounds)  # 由用户设定，不硬限制上限
    job = _mgr.start(
        req.entity_name.strip(),
        hints=req.hints.strip(),
        goal=req.goal.strip(),
        max_rounds=rounds,
        plan_provider=provider,
        adjustable=req.adjustable,
    )
    return {"job_id": job.id, "status": job.status, "plan_provider": provider or "auto",
            "adjustable": req.adjustable}


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


@app.get("/api/jobs/{job_id}/logs")
def get_logs(job_id: str, limit: int = 200, level: str = ""):
    """P4.1: 调查日志查看器 — 读 data/logs/investigation_{job_id}.jsonl

    level: info/debug 过滤（默认全部）"""
    from pathlib import Path
    log_file = Path(__file__).resolve().parent.parent / "data" / "logs" / f"investigation_{job_id}.jsonl"
    if not log_file.exists():
        raise HTTPException(404, f"日志不存在: {job_id}")
    records = []
    for line in log_file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if level and rec.get("level") != level:
            continue
        records.append(rec)
    return {"job_id": job_id, "total": len(records), "records": records[-limit:]}


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


# ── P2.3 中途调整方向 ─────────────────────────────────

class AdjustRequest(BaseModel):
    instruction: str = ""   # 调整指令（如"别追争议了，专注资金链"）


@app.post("/api/jobs/{job_id}/adjust")
def adjust_job(job_id: str, req: AdjustRequest):
    """P2.3: 用户中途调整调查方向（analyze_leads 暂停点 resume）"""
    try:
        job = _mgr.adjust_direction(job_id, req.instruction)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    return job.to_dict()


@app.post("/api/jobs/{job_id}/continue")
def continue_job(job_id: str):
    """P2.3: 用户在暂停点选择继续（不调整）"""
    try:
        job = _mgr.continue_direction(job_id)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    return job.to_dict()


@app.post("/api/jobs/{job_id}/stop")
def stop_job(job_id: str):
    """P2.3: 用户中途停止"""
    try:
        job = _mgr.stop_investigation(job_id)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    return job.to_dict()


# ── 工具注册表 API（P1.1）─────────────────────────────

@app.get("/api/tools")
def list_tools(category: str = ""):
    """工具注册表清单（7 爬虫 + 处理步骤，LLM function calling 用）"""
    from shared.tools.registry import get_registry
    reg = get_registry()
    tools = reg.list_tools(category=category or None)
    return {"tools": tools, "total": len(tools)}


@app.get("/api/tools/audit")
def tool_audit(limit: int = 50, job_id: str = ""):
    """工具调用审计（P1.3 完整回放：输入/输出/耗时/参数）。
    job_id 指定时读磁盘 data/audit/{job_id}.jsonl（跨进程/重启后仍可回放）"""
    from shared.tools.registry import get_registry
    return {"records": get_registry().audit(limit=limit, job_id=job_id), "job_id": job_id}


# ── 动态工具 API（P2.1 Phase C）────────────────────────

class DynamicToolRequest(BaseModel):
    requirement: str           # 需求描述（如"抓取某某网站的公司新闻"）
    name: str = ""             # 可选，工具名（缺省自动猜）
    approval_mode: str = ""    # manual(人审) / auto(LLM安全审查) / 空=config默认
    max_review_rounds: int = 3 # auto 模式最多审查轮数


@app.post("/api/tools/dynamic/generate")
def dynamic_generate(req: DynamicToolRequest):
    """调用 Docker Hermes 生成新爬虫代码。
    approval_mode=auto: LLM 安全审查 → 通过自动注册；发现问题反馈 Hermes 修改循环
    approval_mode=manual(默认): 保存 pending 待用户审批"""
    if not req.requirement.strip():
        raise HTTPException(400, "requirement 不能为空")
    mode = (req.approval_mode or "").strip().lower()
    if mode and mode not in ("manual", "auto"):
        raise HTTPException(400, f"approval_mode 非法: {mode}（可选 manual/auto）")
    from shared.tools.dynamic import get_dynamic_manager
    try:
        tool = get_dynamic_manager().generate(
            req.requirement.strip(), name=req.name,
            approval_mode=mode, max_review_rounds=req.max_review_rounds,
        )
        if tool.status == "approved":
            return {"name": tool.name, "status": tool.status,
                    "message": "auto 审批通过，已注册为 dynamic_<name> 工具",
                    "review_rounds": tool.review_rounds}
        return {"name": tool.name, "status": tool.status, "message": "代码已生成，待审批"}
    except Exception as e:
        raise HTTPException(500, f"动态工具生成失败: {e}")


@app.get("/api/tools/dynamic")
def dynamic_list(status: str = ""):
    """动态工具列表（status: pending/approved/rejected）"""
    from shared.tools.dynamic import get_dynamic_manager
    return {"tools": get_dynamic_manager().list(status=status)}


@app.get("/api/tools/dynamic/{name}")
def dynamic_get(name: str):
    """查看动态工具详情（含源码，审批前审阅用）"""
    from shared.tools.dynamic import get_dynamic_manager
    tool = get_dynamic_manager().get(name)
    if not tool:
        raise HTTPException(404, f"动态工具不存在: {name}")
    return tool.to_dict()


@app.post("/api/tools/dynamic/{name}/approve")
def dynamic_approve(name: str):
    """审批通过：加载代码 + health_check + 注册进 Tool Registry"""
    from shared.tools.dynamic import get_dynamic_manager
    from shared.tools.registry import get_registry
    try:
        tool = get_dynamic_manager().approve(name, registry=get_registry())
        return {"name": tool.name, "status": tool.status, "message": "已注册为 dynamic_<name> 工具"}
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except (ValueError, RuntimeError) as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"审批失败: {e}")


@app.post("/api/tools/dynamic/{name}/reject")
def dynamic_reject(name: str, reason: str = ""):
    """拒绝：标记 rejected，不加载执行"""
    from shared.tools.dynamic import get_dynamic_manager
    try:
        tool = get_dynamic_manager().reject(name, reason)
        return {"name": tool.name, "status": tool.status, "message": "已拒绝"}
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))


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

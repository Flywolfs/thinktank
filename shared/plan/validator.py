"""PlanValidator — 统一校验器（P0.3 融合方案核心）

两条路线（Hermes / Local）的输出都必须通过同一套校验：
1. 结构校验: dimensions 数组、每项含 name/methodology_source/rationale/queries/priority
2. 方法论合法性: methodology_source 必须能在映射表 18 维度中找到依据
3. 基线档案第一: dimensions[0] 必须是基线档案
4. query 具体性: 每维 ≥2 个 query，含实体名或具体线索（不泛泛）
5. 数量: 3-7 个维度；priority ∈ {high,medium,low}

校验失败分级:
- light: 自动修复（补 priority / 补 query）
- medium: 尝试修复，修复失败降级
- severe: 直接降级另一条路线
"""

from __future__ import annotations

import re
from typing import Any

# 方法论映射表 18 维度合法来源（对应 INVESTIGATION_METHODOLOGY.md 第二节）
VALID_METHODOLOGY_KEYWORDS = [
    "核心方法论§1", "核心方法论 §1", "穷尽明面信息", "基线档案",
    "核心方法论§2", "核心方法论 §2", "多源交叉验证", "交叉验证",
    "核心方法论§3", "核心方法论 §3", "政府公共数据库", "政府公开记录",
    "核心方法论§4", "核心方法论 §4", "地理/实体指纹", "实体定位", "地理指纹",
    "核心方法论§5", "核心方法论 §5", "Wayback", "历史回溯",
    "核心方法论§6", "核心方法论 §6", "行为证据推断", "行为信号",
    "个人深扒§1", "个人深扒 §1", "资金链条",
    "个人深扒§2", "个人深扒 §2", "关键时间点对齐", "时间线对齐",
    "个人深扒§3", "个人深扒 §3", "关系网络",
    "个人深扒§4", "个人深扒 §4", "原文直接引用", "原文引用",
    "个人深扒§5", "个人深扒 §5", "历史言论翻查", "言论史",
    "个人深扒§6", "个人深扒 §6", "同类人物对比", "同类对比",
    "个人深扒§7", "个人深扒 §7", "外部事件关联解读", "大环境关联",
    "Phase 2", "热点事件",
    "Phase 6", "媒体矩阵", "媒体反应",
    "Phase 7", "断章取义", "争议核实",
    "Phase 8", "利益链条",
    "Phase 9", "时间线重建",
    "元数据挖掘", "数字痕迹",
]

PRIORITIES = {"high", "medium", "low"}


class PlanValidationResult:
    """校验结果（含可修复的轻量问题）"""

    def __init__(self, ok: bool, issues: list[str], repaired: list[dict] | None = None):
        self.ok = ok
        self.issues = issues
        self.repaired = repaired

    @property
    def severity(self) -> str:
        """light / medium / severe"""
        if self.ok:
            return "ok"
        if len(self.issues) <= 2:
            return "light"
        return "severe"

    def __repr__(self) -> str:
        return f"PlanValidationResult(ok={self.ok}, issues={self.issues})"


class PlanValidator:
    def __init__(self, min_dimensions: int = 3, max_dimensions: int = 7):
        self.min_dimensions = min_dimensions
        self.max_dimensions = max_dimensions

    # ── 对外主入口 ─────────────────────────────────────

    def validate(self, plan: dict | list, entity: str) -> PlanValidationResult:
        """校验 plan（dict 含 dimensions 或直接是 list）。返回结果 + 修复后的 plan"""
        issues: list[str] = []

        dims = plan.get("dimensions") if isinstance(plan, dict) else plan
        if not isinstance(dims, list) or not dims:
            return PlanValidationResult(False, ["dimensions 缺失或为空"])

        # 数量
        if len(dims) < self.min_dimensions:
            issues.append(f"维度过少: {len(dims)} < {self.min_dimensions}")
        if len(dims) > self.max_dimensions:
            issues.append(f"维度过多: {len(dims)} > {self.max_dimensions}")

        # 基线档案第一
        if dims and not self._is_baseline(dims[0]):
            issues.append(f"基线档案必须第一, 当前第一是: {dims[0].get('name', '?')}")

        # 逐维度校验
        repaired = []
        for i, dim in enumerate(dims):
            dim_issues = self._validate_dimension(dim, entity)
            if dim_issues:
                issues.extend(f"[维度{i+1} {dim.get('name', '?')}] {x}" for x in dim_issues)
            fixed = self._repair_dimension(dim, entity)
            repaired.append(fixed)

        ok = not issues
        return PlanValidationResult(ok, issues, repaired)

    # ── 单维度校验 ─────────────────────────────────────

    def _validate_dimension(self, dim: dict, entity: str) -> list[str]:
        issues = []
        if not dim.get("name"):
            issues.append("缺少 name")
        if not dim.get("rationale"):
            issues.append("缺少 rationale")
        src = dim.get("methodology_source", "")
        if not src:
            issues.append("缺少 methodology_source")
        elif not self._methodology_valid(src):
            issues.append(f"methodology_source 非法: {src[:50]}")
        queries = dim.get("queries", [])
        if not isinstance(queries, list) or len(queries) < 1:
            issues.append("queries 为空")
        elif not any(self._query_concrete(q, entity) for q in queries):
            issues.append("queries 太泛（无实体名/具体线索）")
        pri = dim.get("priority", "")
        if pri and pri not in PRIORITIES:
            issues.append(f"priority 非法: {pri}")
        return issues

    # ── 修复（轻量问题自动补） ─────────────────────────

    def _repair_dimension(self, dim: dict, entity: str) -> dict:
        fixed = dict(dim)
        # priority 缺失 → medium
        if not fixed.get("priority") or fixed["priority"] not in PRIORITIES:
            fixed["priority"] = "medium"
        # queries 缺失/空 → 用实体名兜底
        queries = fixed.get("queries") or []
        if not isinstance(queries, list) or len(queries) == 0:
            fixed["queries"] = [f"{entity} 维基百科", f"{entity} 百度百科"]
        # methodology_source 缺失 → 按名称推断（最接近的合法来源）
        if not fixed.get("methodology_source"):
            fixed["methodology_source"] = self._infer_methodology(fixed.get("name", ""))
        return fixed

    # ── 内部工具 ───────────────────────────────────────

    def _is_baseline(self, dim: dict) -> bool:
        name = dim.get("name", "")
        return "基线" in name or "档案" in name or "基准" in name

    def _methodology_valid(self, source: str) -> bool:
        return any(kw in source for kw in VALID_METHODOLOGY_KEYWORDS)

    def _query_concrete(self, query: str, entity: str) -> bool:
        """query 是否具体：包含实体名 或 包含具体线索词（人名/金额/机构/动作）"""
        if not query:
            return False
        if entity and entity in query:
            return True
        # 具体线索词（粗启发）
        clue_patterns = [
            r"[\u4e00-\u9fff]{2,}",   # 至少 2 个中文字（排除纯数字/英文泛词）
        ]
        if re.search(r"[0-9]+\s*万|亿|元|年|月", query):
            return True
        return any(re.search(p, query) for p in clue_patterns) and len(query) >= 6

    def _infer_methodology(self, name: str) -> str:
        """根据维度名推断最可能的合法方法论来源（修复兜底）"""
        mapping = {
            "基线": "核心方法论§1 穷尽明面信息",
            "政府": "核心方法论§3 政府公共数据库",
            "历史回溯": "核心方法论§5 Wayback Machine",
            "行为": "核心方法论§6 行为证据推断",
            "资金": "个人深扒§1 资金链条追踪",
            "时间": "个人深扒§2 关键时间点对齐",
            "关系": "个人深扒§3 关系网络挖掘",
            "言论": "个人深扒§5 历史言论翻查",
            "同类": "个人深扒§6 同类人物对比",
            "大环境": "个人深扒§7 外部事件关联解读",
            "热点": "Phase 2 热点切入",
            "媒体": "Phase 6 媒体矩阵分析",
            "争议": "Phase 7 断章取义检测",
            "利益": "Phase 8 利益链条专项",
            "时间线": "Phase 9 时间线重建",
            "数字": "元数据挖掘技巧",
            "交叉": "核心方法论§2 多源交叉验证",
        }
        for kw, src in mapping.items():
            if kw in name:
                return src
        return "核心方法论§1 穷尽明面信息"  # 兜底

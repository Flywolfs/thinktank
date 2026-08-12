"""PlanProvider — 双路线策略选择器（P0.3 融合方案）

模式:
- auto (默认): Hermes 优先 → 校验失败/超时/异常 → 降级 Local
- hermes: 强制 Hermes，失败抛错（不静默降级）
- local: 只用自研 plan_node

统一输出: {dimensions, max_rounds, plan_summary, provider}
所有路线输出都必须通过 PlanValidator 校验（Hermes 的聪明 + 系统的确定性约束）。
"""

from __future__ import annotations

import time
from typing import Any, Callable

from shared.plan.validator import PlanValidationResult, PlanValidator
from shared.utils import config
from shared.utils.logger import get_current_logger

# Local 生成函数签名: (entity, hints, goal) -> dict（复用 graph.py 的 plan_node 步骤）
LocalGenerator = Callable[[str, str, str], dict]


class PlanGenerationError(Exception):
    """plan 生成失败（所有路线都失败时抛出）"""


class PlanProvider:
    def __init__(
        self,
        mode: str = "",
        validator: PlanValidator | None = None,
        hermes: Any | None = None,   # HermesPlanProvider 实例（注入便于测试）
        local_generator: LocalGenerator | None = None,
    ):
        self.mode = (mode or config.PLAN_PROVIDER).lower()
        if self.mode not in ("auto", "hermes", "local"):
            self.mode = "auto"
        self.validator = validator or PlanValidator()
        self.hermes = hermes
        self.local_generator = local_generator

    # ── 对外主入口 ─────────────────────────────────────

    def generate(self, entity: str, hints: str = "", goal: str = "") -> dict:
        """按模式生成 plan，返回统一结构 + 校验结果"""
        logger = get_current_logger()
        t0 = time.time()
        plan: dict | None = None
        provider_used = ""

        if self.mode in ("auto", "hermes"):
            try:
                plan, provider_used = self._try_hermes(entity, hints, goal)
            except Exception as e:
                if logger:
                    logger.info(
                        "plan_provider",
                        f"Hermes 路线失败: {e}",
                        detail={"entity": entity, "mode": self.mode, "error": str(e)[:200]},
                    )
                if self.mode == "hermes":
                    raise PlanGenerationError(f"Hermes 路线失败（强制模式）: {e}") from e
                # auto 模式 → 降级 local

        if plan is None:
            if not self.local_generator:
                raise PlanGenerationError("未配置 local_generator，无法降级")
            plan, provider_used = self._try_local(entity, hints, goal)

        # 统一补全字段
        plan["provider"] = provider_used
        plan["elapsed_s"] = round(time.time() - t0, 1)
        return plan

    # ── 内部 ───────────────────────────────────────────

    def _try_hermes(self, entity: str, hints: str, goal: str) -> tuple[dict, str]:
        """尝试 Hermes 路线；输出必须通过校验，否则视为失败（可降级）"""
        from shared.plan.hermes_provider import HermesPlanProvider, PlanProviderError

        provider = self.hermes or HermesPlanProvider()
        raw = provider.generate_plan(entity, hints, goal)

        # 统一校验（Hermes 的聪明 + 系统的确定性约束）
        result: PlanValidationResult = self.validator.validate(raw, entity)
        if not result.ok:
            # 修复尝试：用 validator 的 repaired 输出
            repaired = result.repaired
            if repaired and len(repaired) >= self.validator.min_dimensions:
                raw["dimensions"] = repaired
                # 修复后二次校验
                result2 = self.validator.validate(raw, entity)
                if result2.ok:
                    return raw, "hermes"
                raw["_validation_issues"] = result.issues
            raise PlanProviderError(f"Hermes 输出未通过校验: {result.issues[:3]}")
        return raw, "hermes"

    def _try_local(self, entity: str, hints: str, goal: str) -> tuple[dict, str]:
        """Local 路线（自研 plan_node）"""
        if not self.local_generator:
            raise PlanGenerationError("未配置 local_generator，无法降级")
        result = self.local_generator(entity, hints, goal)
        if not isinstance(result, dict) or not result.get("dimensions"):
            raise PlanGenerationError(f"Local 路线输出无效: {result}")
        return result, "local"

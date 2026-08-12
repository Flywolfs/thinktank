"""HermesPlanProvider — 路线B: 调用 Docker Hermes API 生成 plan

调用 http://localhost:8643/v1/chat/completions（intel-planner profile），
Hermes 已加载 think-tank-intel 方法论 skill，返回维度模板 JSON。

失败/超时/解析失败 → 抛 PlanProviderError，由上层降级到 Local。
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from shared.utils import config
from shared.utils.logger import get_current_logger


class PlanProviderError(Exception):
    """Plan provider 调用失败（可降级）"""


class HermesPlanProvider:
    def __init__(
        self,
        base_url: str = "",
        api_key: str = "",
        model: str = "",
        timeout: float = 0,
    ):
        self.base_url = (base_url or config.HERMES_API_URL).rstrip("/")
        self.api_key = api_key or config.HERMES_API_KEY
        self.model = model or config.HERMES_PLAN_MODEL
        self.timeout = timeout or config.HERMES_PLAN_TIMEOUT

    # ── 对外接口 ───────────────────────────────────────

    def generate_plan(self, entity: str, hints: str, goal: str) -> dict:
        """调用 Hermes 生成 plan，返回 {dimensions, max_rounds, plan_summary}"""
        logger = get_current_logger()
        if not self.api_key:
            raise PlanProviderError(
                "HERMES_API_KEY 未配置（deploy/intel-planner/.env 的 HERMES_INTEL_API_KEY）"
            )

        prompt = self._build_prompt(entity, hints, goal)
        t0 = time.time()

        try:
            resp = httpx.post(
                f"{self.base_url}/v1/chat/completions",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                },
                json={
                    "model": self.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1,
                },
                timeout=self.timeout,
            )
        except httpx.HTTPError as e:
            raise PlanProviderError(f"Hermes API 请求失败: {e}") from e

        elapsed = time.time() - t0
        if resp.status_code != 200:
            raise PlanProviderError(
                f"Hermes API 返回 {resp.status_code}: {resp.text[:200]}"
            )

        try:
            content = resp.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, json.JSONDecodeError) as e:
            raise PlanProviderError(f"Hermes 响应解析失败: {e}") from e

        plan = self._parse_plan(content, entity)
        if logger:
            logger.info(
                "hermes_plan",
                f"Hermes plan 生成成功: {len(plan.get('dimensions', []))} 维度",
                detail={
                    "entity": entity,
                    "status": "ok",
                    "elapsed_s": round(elapsed, 1),
                    "dimensions": [d.get("name") for d in plan.get("dimensions", [])],
                },
            )
        return plan

    # ── 内部 ───────────────────────────────────────────

    def _build_prompt(self, entity: str, hints: str, goal: str) -> str:
        return f"""你是情报调查计划专家，请严格按照你加载的 think-tank-intel 调查方法论来制定调查计划。

调查核心实体: {entity}
用户关注方向: {hints or "（无）"}
调查意图: {goal or "全面调查"}

请输出该实体的调查维度模板 JSON（注意：必须引用方法论中的具体章节作为 methodology_source，维度数量 3-7 个）:
{{
  "dimensions": [
    {{
      "name": "维度名",
      "methodology_source": "引用的方法论来源章节（如: 核心方法论§1 穷尽明面信息）",
      "rationale": "为什么选这个维度（结合该实体的具体特征）",
      "queries": ["2-4 个具体搜索关键词/角度，要带具体人名/机构/金额/年份等线索"],
      "priority": "high|medium|low"
    }}
  ],
  "max_rounds": 6,
  "plan_summary": "一句话概述调查路径"
}}
只输出 JSON，不要额外解释。"""

    def _parse_plan(self, content: str, entity: str) -> dict:
        """从 Hermes 回复中提取 plan JSON（可能是 ```json 代码块包裹）"""
        text = content.strip()
        if "```" in text:
            # 提取第一个 ```json ... ``` 块
            start = text.find("```json")
            if start == -1:
                start = text.find("```")
            if start != -1:
                start += 3  # 跳过 ```
                if text[start] == "j":
                    start += 4  # 跳过 json
                end = text.find("```", start)
                text = text[start:end].strip() if end != -1 else text[start:].strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise PlanProviderError(f"Hermes 输出非 JSON: {e} | 前200字: {text[:200]}")

        dims = data.get("dimensions") if isinstance(data, dict) else data
        if not isinstance(dims, list) or not dims:
            raise PlanProviderError("Hermes 输出缺少 dimensions")

        return {
            "dimensions": dims,
            "max_rounds": int(data.get("max_rounds") or len(dims) + 1) if isinstance(data, dict) else len(dims) + 1,
            "plan_summary": data.get("plan_summary", "") if isinstance(data, dict) else "",
            "provider": "hermes",
        }

"""DynamicToolManager — Phase C 动态工具创建（P2.1）

流程:
1. 用户/调查提出"需要某网站数据但没爬虫" → 描述需求
2. 调用 Docker Hermes Agent（写代码能力）生成爬虫代码
   （模板约束: 必须实现 BaseProvider 接口 + 工具元信息）
3. 代码保存到 tools/dynamic/{name}.py（未注册，待审批）
4. 审批通过 → importlib 加载 → 校验接口 → 注册进 Tool Registry
   拒绝 → 代码保留但标记 rejected（可查看/重生成）

安全设计（roadmap P2.1）:
- 动态代码是可执行操作 → 必须人审批后才加载执行
- 加载时用 importlib + 接口校验（只接受 BaseProvider 子类/标准函数）
- 生成代码前展示给用户（GET 查看源码）
"""

from __future__ import annotations

import importlib.util
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import httpx

from shared.utils import config

DEFAULT_DYNAMIC_DIR = Path(__file__).resolve().parent.parent.parent / "tools" / "dynamic"

# 动态工具的代码模板（约束 Hermes 生成的代码结构）
TOOL_TEMPLATE = '''"""动态工具: {name} — {description}（由 Hermes Agent 生成，需审批后启用）"""

from datetime import datetime
from shared.crawlers.base import SearchParams, SearchResult

TOOL_NAME = "{name}"
TOOL_DESCRIPTION = "{description}"
TOOL_CATEGORY = "crawler"
TOOL_COST = "medium"


class {class_name}(object):
    """{description}"""

    def __init__(self):
        # 在这里初始化客户端/配置（如从 shared.utils.config 读取 key）
        pass

    def health_check(self) -> bool:
        """验证数据源是否可用（审批通过后注册时调用）"""
        return True

    def search(self, params: SearchParams) -> list[SearchResult]:
        """实现搜索逻辑，返回统一 SearchResult 列表。

        params.query      — 搜索关键词
        params.max_results — 最大返回条数
        params.time_start / time_end — 时间范围（None=不限）

        要求:
        1. 用 httpx/requests 调目标网站或 API
        2. 解析结果并构造 SearchResult(source="{name}", source_type="news|social|...", ...)
        3. 失败时返回空列表（不要抛异常导致整轮搜索失败）
        """
        # TODO: 在这里实现爬虫逻辑
        return []


def get_provider():
    """注册时调用：返回 BaseProvider 兼容实例"""
    return {class_name}()
'''


@dataclass
class DynamicTool:
    """一个动态工具的完整状态"""
    name: str
    description: str
    status: str = "pending"        # pending(待审) / approved(已注册) / rejected(已拒绝)
    code: str = ""
    created_at: float = field(default_factory=time.time)
    generated_by: str = "hermes"   # hermes / manual
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "status": self.status,
            "code": self.code,
            "created_at": self.created_at,
            "generated_by": self.generated_by,
            "error": self.error,
        }


class DynamicToolManager:
    """动态工具管理：生成 → 保存 → 审批 → 加载注册"""

    def __init__(self, dynamic_dir: Path | None = None):
        self.dir = dynamic_dir or DEFAULT_DYNAMIC_DIR
        self.dir.mkdir(parents=True, exist_ok=True)

    # ── 路径 ───────────────────────────────────────────

    def _code_path(self, name: str) -> Path:
        return self.dir / f"{name}.py"

    def _meta_path(self, name: str) -> Path:
        return self.dir / f"{name}.meta.json"

    # ── 生成（调 Hermes） ──────────────────────────────

    def generate(self, requirement: str, name: str = "") -> DynamicTool:
        """调用 Docker Hermes 生成爬虫代码，保存为 pending 状态"""
        from shared.plan.hermes_provider import PlanProviderError

        name = self._sanitize_name(name or self._guess_name(requirement))
        code = self._call_hermes_generate(requirement, name)
        if not code:
            raise PlanProviderError("Hermes 未返回代码")

        tool = DynamicTool(name=name, description=requirement[:100], code=code)
        self._save(tool)
        return tool

    def _call_hermes_generate(self, requirement: str, name: str) -> str:
        """调 Hermes /v1/chat/completions 生成代码"""
        if not config.HERMES_API_KEY:
            raise RuntimeError("HERMES_API_KEY 未配置（deploy/intel-planner/.env）")

        class_name = self._to_class_name(name)
        template = TOOL_TEMPLATE.format(
            name=name, description=requirement[:80], class_name=class_name,
        )
        prompt = f"""你是一个爬虫开发专家。请为情报系统开发一个新的数据源爬虫。

需求: {requirement}
工具名: {name}
类名: {class_name}

【必须遵守的代码模板】（保持结构和类名不变，只填充 search() 实现）:
```python
{template}
```

要求:
1. 保持类名 {class_name}、方法签名 search(self, params: SearchParams) -> list[SearchResult] 不变
2. 用 httpx 或 requests 实现实际爬取（不要 TODO）
3. 解析后构造 SearchResult 列表，source_type 用 news/social/knowledge/enterprise 之一
4. health_check() 返回真实可用性判断
5. 失败时返回空列表，不抛异常
6. 只输出 Python 代码，不要额外解释，不要 markdown 代码块标记
"""
        resp = httpx.post(
            f"{config.HERMES_API_URL}/v1/chat/completions",
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {config.HERMES_API_KEY}"},
            json={"model": config.HERMES_PLAN_MODEL,
                  "messages": [{"role": "user", "content": prompt}],
                  "temperature": 0.1},
            timeout=config.HERMES_PLAN_TIMEOUT,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        # 清理 markdown 代码块标记（```python ... ``` 或 ``` ... ```）
        if "```" in content:
            parts = content.split("```")
            for p in parts:
                candidate = p.strip()
                if candidate.startswith("python\n"):
                    candidate = candidate[len("python\n"):].strip()
                if "class " in candidate or "def " in candidate:
                    content = candidate
                    break
        return content.strip()

    # ── 审批 ───────────────────────────────────────────

    def list(self, status: str = "") -> list[dict]:
        """列出动态工具（默认全部；status 可过滤 pending/approved/rejected）"""
        out = []
        for p in sorted(self.dir.glob("*.meta.json")):
            try:
                meta = json.loads(p.read_text(encoding="utf-8"))
                if status and meta.get("status") != status:
                    continue
                out.append(meta)
            except Exception:
                continue
        return out

    def get(self, name: str) -> DynamicTool | None:
        p = self._meta_path(name)
        if not p.exists():
            return None
        meta = json.loads(p.read_text(encoding="utf-8"))
        # meta 可能已含 code 字段（to_dict 序列化时），从文件读最新代码覆盖
        code = self._code_path(name).read_text(encoding="utf-8") if self._code_path(name).exists() else ""
        meta["code"] = code
        return DynamicTool(**meta)

    def approve(self, name: str, registry=None) -> DynamicTool:
        """批准：加载代码 + 校验接口 + 注册进 Tool Registry"""
        from shared.tools.registry import ToolSpec

        tool = self.get(name)
        if not tool:
            raise FileNotFoundError(f"动态工具不存在: {name}")
        if tool.status != "pending":
            raise ValueError(f"工具状态不是 pending: {tool.status}")

        # 1. importlib 加载
        provider_cls = self._load_provider(tool)
        # 2. 实例化 + health_check
        provider = provider_cls()
        if not provider.health_check():
            tool.error = "health_check 失败，数据源不可用"
            self._save(tool)
            raise RuntimeError(tool.error)
        # 3. 注册进 registry（如果传入）
        if registry is not None:
            registry.register(
                ToolSpec(
                    name=f"dynamic_{tool.name}",
                    description=tool.description,
                    category="crawler",
                    cost="medium",
                    provider=f"dynamic:{tool.name}",
                ),
                lambda query, max_results=10: _dynamic_search(provider, query, max_results),
            )
        # 4. 更新状态
        tool.status = "approved"
        tool.error = ""
        self._save(tool)
        return tool

    def reject(self, name: str, reason: str = "") -> DynamicTool:
        tool = self.get(name)
        if not tool:
            raise FileNotFoundError(f"动态工具不存在: {name}")
        tool.status = "rejected"
        tool.error = reason
        self._save(tool)
        return tool

    # ── 内部 ───────────────────────────────────────────

    def _load_provider(self, tool: DynamicTool):
        """importlib 加载动态工具模块，返回 provider 类"""
        # 写回代码文件（审批时确保最新）
        self._code_path(tool.name).write_text(tool.code, encoding="utf-8")
        spec = importlib.util.spec_from_file_location(
            f"tools_dynamic_{tool.name}", self._code_path(tool.name)
        )
        if spec is None or spec.loader is None:
            raise RuntimeError(f"无法加载动态工具模块: {tool.name}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        # 期望类名
        class_name = self._to_class_name(tool.name)
        cls = getattr(mod, class_name, None)
        if cls is None:
            raise RuntimeError(f"模块中未找到类 {class_name}")
        return cls

    def _save(self, tool: DynamicTool) -> None:
        """保存代码 + 元数据"""
        self._code_path(tool.name).write_text(tool.code, encoding="utf-8")
        self._meta_path(tool.name).write_text(
            json.dumps(tool.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _sanitize_name(self, name: str) -> str:
        """工具名规范化：小写字母数字下划线"""
        import re
        return re.sub(r"[^a-z0-9_]", "_", name.lower()).strip("_") or "dynamic_tool"

    def _to_class_name(self, name: str) -> str:
        """snake_case → PascalCase"""
        return "".join(part.capitalize() for part in name.split("_"))

    def _guess_name(self, requirement: str) -> str:
        """从需求描述猜工具名（简单启发）"""
        import re
        # 找"XX网站/XX平台"的 XX
        m = re.search(r"([\u4e00-\u9fff]+)(?:网站|平台|数据)", requirement)
        if m:
            return self._sanitize_name(m.group(1))
        return "dynamic_tool"


_default_manager: DynamicToolManager | None = None


def get_dynamic_manager() -> DynamicToolManager:
    """全局动态工具管理器单例"""
    global _default_manager
    if _default_manager is None:
        _default_manager = DynamicToolManager()
    return _default_manager


def _dynamic_search(provider, query: str, max_results: int = 10):
    """动态工具注册后的调用入口（构造 SearchParams → provider.search）"""
    from shared.crawlers.base import SearchParams
    return provider.search(SearchParams(query=query, max_results=max_results))

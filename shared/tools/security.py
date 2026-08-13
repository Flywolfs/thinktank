"""CodeSecurityReviewer — 动态工具代码安全审查（P2.1 auto 审批模式）

两层审查:
1. 规则粗查（快速、确定）：检查危险操作模式
   - 文件系统破坏（rm -rf / shutil.rmtree / os.remove 等）
   - 命令执行（os.system / subprocess / eval / exec）
   - 敏感文件读取（/etc/passwd、~/.ssh、.env、credentials）
   - 网络外连（向非目标站发请求、内网地址探测）
   - 代码混淆（base64 decode 后执行、反引号等）
2. LLM 精查（慢、语义级）：规则查不出的人为恶意逻辑
   - 用专门的安全审查 prompt，让 LLM 判断"这个爬虫代码是否有恶意/危险行为"
   - 返回 issues 列表 + 是否通过

auto 模式流程: 审查 → 发现问题 → 反馈给写代码的 Hermes 修改 → 重新审查（循环）
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import httpx

from shared.utils import config
from shared.utils.logger import get_current_logger

# ── 规则粗查：危险模式 ─────────────────────────────────

DANGEROUS_PATTERNS = [
    # 文件系统破坏
    (r"rm\s+-rf", "递归删除文件/目录 (rm -rf)"),
    (r"shutil\.rmtree", "递归删除目录 (shutil.rmtree)"),
    (r"os\.remove\s*\(|os\.unlink\s*\(", "删除文件 (os.remove/unlink)"),
    (r"pathlib.*\.unlink\s*\(", "删除文件 (Path.unlink)"),
    (r"os\.makedirs.*exist_ok=False", "创建目录但可能抛错"),
    # 命令执行
    (r"os\.system\s*\(", "执行系统命令 (os.system)"),
    (r"subprocess\.(run|Popen|call|check_output)\s*\(", "执行子进程 (subprocess)"),
    (r"\beval\s*\(|\bexec\s*\(", "动态执行代码 (eval/exec)"),
    (r"__import__\s*\(", "动态导入 (__import__)"),
    (r"pickle\.(load|loads)", "反序列化 (pickle.load)"),
    # 敏感文件
    (r"/etc/passwd|/etc/shadow", "读取系统账户文件"),
    (r"\.ssh/|id_rsa|id_ed25519", "读取 SSH 私钥"),
    (r"\.env\b|credentials|secret|token|password\s*=", "读取敏感配置/密钥"),
    (r"getpass|/proc/", "读取进程/凭据信息"),
    # 网络异常
    (r"127\.0\.0\.1|localhost.*(?:3306|5432|6379|27017|9200)", "连接本地数据库端口"),
    (r"169\.254\.169\.254", "云元数据服务（SSRF 风险）"),
    (r"10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.", "内网地址探测"),
    (r"socket\.socket", "原始 socket 连接"),
    (r"requests\.(get|post).*http://(?!https)", "明文 HTTP 请求（可能被篡改）"),
    # 混淆
    (r"base64\.b64decode.*exec|exec.*base64", "base64 编码后执行"),
    (r"\\x[0-9a-fA-F]{2}", "十六进制转义代码（可能混淆）"),
]

# 允许但需留意的模式（不直接判定危险，但 LLM 精查时重点看）
SUSPICIOUS_PATTERNS = [
    (r"webhook|callback\s*=|notify.*url", "可能向第三方回调地址发数据"),
    (r"upload|/api/.*write|POST.*create", "可能向目标站写入数据"),
    (r"session|login|cookie", "可能操作登录态（需确认目标站）"),
]


@dataclass
class SecurityReviewResult:
    """安全审查结果"""
    passed: bool
    issues: list[str] = field(default_factory=list)
    method: str = "rules"          # rules / llm / rules+llm
    review_log: str = ""           # 给 Hermes 的修改反馈（含修复建议）

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "issues": self.issues,
            "method": self.method,
        }


class CodeSecurityReviewer:
    def __init__(self, use_llm: bool = True, max_issues_log: int = 5):
        self.use_llm = use_llm
        self.max_issues_log = max_issues_log

    # ── 主入口 ─────────────────────────────────────────

    def review(self, code: str, name: str = "") -> SecurityReviewResult:
        """对动态工具代码做安全审查。规则粗查 → LLM 精查。"""
        logger = get_current_logger()

        # 1. 规则粗查
        rule_issues = self._rule_scan(code)
        result = SecurityReviewResult(
            passed=not rule_issues,
            issues=rule_issues[: self.max_issues_log],
            method="rules",
        )

        # 2. LLM 精查（规则通过 或 规则有疑点 都做语义级判断）
        if self.use_llm and config.DEEPSEEK_API_KEY:
            try:
                llm_issues, passed = self._llm_review(code, name)
                result.issues = list(dict.fromkeys(result.issues + llm_issues))[: self.max_issues_log]
                result.passed = passed and not rule_issues
                result.method = "rules+llm"
            except Exception as e:
                if logger:
                    logger.info("security_review", f"LLM 审查失败，仅用规则: {e}")
                # LLM 失败不阻塞——规则已过则算过（保守策略在规则层）

        # 生成给 Hermes 的修改反馈
        result.review_log = self._build_feedback(result)
        if logger:
            logger.info(
                "security_review",
                f"代码安全审查: {'通过' if result.passed else '发现问题'} "
                f"({result.method}) {len(result.issues)} 个问题",
                detail={"tool": name, "passed": result.passed, "issues": result.issues},
            )
        return result

    # ── 规则层 ─────────────────────────────────────────

    def _rule_scan(self, code: str) -> list[str]:
        """正则扫描危险模式"""
        issues = []
        for pattern, desc in DANGEROUS_PATTERNS:
            if re.search(pattern, code, re.IGNORECASE):
                issues.append(f"危险模式: {desc} ({pattern})")
        return issues

    # ── LLM 层 ─────────────────────────────────────────

    def _llm_review(self, code: str, name: str) -> tuple[list[str], bool]:
        """调 DeepSeek 语义级安全审查"""
        prompt = f"""你是代码安全审查专家。下面是待注册的动态爬虫工具代码（{name or "unknown"}）。
请判断这段代码是否存在【恶意或危险行为】——不是爬虫功能本身的网络请求，
而是: 删除/修改文件、执行任意命令、读取敏感信息（密钥/密码/私钥）、
连接内网/云元数据、数据外泄给第三方、代码混淆隐藏恶意逻辑等。

代码:
```python
{code[:8000]}
```

输出 JSON:
{{
  "passed": true/false,
  "issues": [
    {{"severity": "high|medium|low", "description": "问题描述", "suggestion": "修复建议"}}
  ]
}}

规则:
- 爬虫正常的 HTTP 请求（get/post 目标网站）不算危险
- 只有文件破坏/命令执行/敏感读取/SSRF/数据外泄/混淆才算
- passed=false 当且仅当存在 high 或 medium 问题
"""
        resp = httpx.post(
            "https://api.deepseek.com/chat/completions",
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {config.DEEPSEEK_API_KEY}"},
            json={
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
                "response_format": {"type": "json_object"},
            },
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()["choices"][0]["message"]["content"]
        import json as _json
        try:
            parsed = _json.loads(data)
        except Exception:
            return [], True  # 解析失败 → 保守放行（规则层已把关）

        issues = [
            f"[{i.get('severity', 'low')}] {i.get('description', '')} → {i.get('suggestion', '')}"
            for i in parsed.get("issues", [])
            if i.get("description") and i.get("severity") in ("high", "medium")
        ]
        return issues, parsed.get("passed", True)

    # ── 反馈生成 ───────────────────────────────────────

    def _build_feedback(self, result: SecurityReviewResult) -> str:
        """生成给写代码的 Hermes 的修改反馈（供 auto 模式迭代）"""
        if result.passed:
            return "代码已通过安全审查，无需修改。"
        lines = [
            "你的代码存在以下安全/危险行为，请修改后重新写入文件:",
            "",
        ]
        for issue in result.issues:
            lines.append(f"- {issue}")
        lines.append("")
        lines.append("要求: 保持爬虫功能不变，去掉危险操作，重新写入 /opt/tools_dynamic/ 下原文件。")
        return "\n".join(lines)


_default_reviewer: CodeSecurityReviewer | None = None


def get_security_reviewer() -> CodeSecurityReviewer:
    global _default_reviewer
    if _default_reviewer is None:
        _default_reviewer = CodeSecurityReviewer()
    return _default_reviewer

"""统一配置加载 — 从 .env 文件读取所有环境变量"""

import os
from pathlib import Path

from dotenv import load_dotenv

# 自动加载项目根目录的 .env（shared/utils/config.py → shared/ → 项目根）
_project_root = Path(__file__).resolve().parent.parent.parent
load_dotenv(_project_root / ".env")


def get(key: str, default: str = "") -> str:
    """读取环境变量，不存在时返回默认值"""
    return os.getenv(key, default)


def require(key: str) -> str:
    """读取必须的环境变量，不存在时抛出异常"""
    value = os.getenv(key)
    if not value:
        raise ValueError(
            f"环境变量 {key} 未设置。请在项目根目录的 .env 文件中配置。\n"
            f"参考 .env.example 获取说明。"
        )
    return value


# ── 搜索引擎 ──────────────────────────────────────────

SERPER_API_KEY = get("SERPER_API_KEY")
SERPAPI_API_KEY = get("SERPAPI_API_KEY")
BING_API_KEY = get("BING_API_KEY")

# ── LLM ────────────────────────────────────────────────

DEEPSEEK_API_KEY = get("DEEPSEEK_API_KEY")
DOUBAO_API_KEY = get("DOUBAO_API_KEY")

# ── ASR ────────────────────────────────────────────────

QWEN3_ASR_URL = get("QWEN3_ASR_URL", "http://localhost:8000")

# ── 数据库 ──────────────────────────────────────────────

NEO4J_URI = get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = get("NEO4J_PASSWORD", "")
DATABASE_URL = get("DATABASE_URL", "postgresql://thinktank:@localhost:5432/thinktank")
REDIS_URL = get("REDIS_URL", "redis://localhost:6379/0")

# ── RSSHub（备用）──────────────────────────────────────

RSSHUB_URL = get("RSSHUB_URL", "http://localhost:1200")

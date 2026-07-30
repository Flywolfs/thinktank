"""实体搜索结果的数据模型"""

from dataclasses import dataclass, field
from datetime import datetime

from shared.crawlers.base import SearchResult


@dataclass
class EntityReport:
    """一次实体搜索的完整聚合报告"""

    # 搜索元信息
    entity_name: str
    searched_at: datetime = field(default_factory=datetime.now)
    search_depth: int = 1
    total_results: int = 0

    # 各源原始结果
    web_results: list[SearchResult] = field(default_factory=list)
    knowledge_results: list[SearchResult] = field(default_factory=list)
    social_results: list[SearchResult] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)  # source → error_msg

    # LLM 抽取后的结构化数据（Phase 1 填充）
    core_summary: str = ""                # 实体概述
    related_entities: list[dict] = field(default_factory=list)
    # [{name, type, relation, source, confidence}, ...]

    def to_dict(self) -> dict:
        return {
            "entity_name": self.entity_name,
            "searched_at": self.searched_at.isoformat(),
            "search_depth": self.search_depth,
            "total_results": self.total_results,
            "web_results_count": len(self.web_results),
            "knowledge_results_count": len(self.knowledge_results),
            "social_results_count": len(self.social_results),
            "errors": self.errors,
            "core_summary": self.core_summary,
            "related_entities": self.related_entities,
        }

    def print_summary(self) -> str:
        """生成可读摘要"""
        lines = [
            f"═══════════════════════════════════",
            f"  实体情报报告: {self.entity_name}",
            f"═══════════════════════════════════",
            f"  搜索时间: {self.searched_at.strftime('%Y-%m-%d %H:%M')}",
            f"  深度: {self.search_depth}",
            f"  结果总计: {self.total_results} 条",
            f"",
        ]

        if self.core_summary:
            lines.append(f"  📝 概述: {self.core_summary[:200]}...")
            lines.append("")

        if self.knowledge_results:
            lines.append(f"  📚 知识库 ({len(self.knowledge_results)} 条):")
            for r in self.knowledge_results[:3]:
                lines.append(f"     [{r.source}] {r.title}")
                lines.append(f"     {r.content[:150]}...")
            lines.append("")

        if self.web_results:
            lines.append(f"  🌐 网页搜索 ({len(self.web_results)} 条):")
            for r in self.web_results[:5]:
                lines.append(f"     [{r.metadata.get('position', '?')}] {r.title[:60]}")
                lines.append(f"     {r.url}")
            lines.append("")

        if self.social_results:
            lines.append(f"  📹 社交媒体 ({len(self.social_results)} 条):")
            for r in self.social_results[:5]:
                lines.append(f"     [{r.source}] {r.title[:50]}")
                if r.metadata.get("play"):
                    lines.append(f"     播放: {r.metadata['play']} | UP: {r.author}")
            lines.append("")

        if self.errors:
            lines.append(f"  ⚠️ 错误 ({len(self.errors)}):")
            for src, err in self.errors.items():
                lines.append(f"     {src}: {err[:80]}")

        lines.append(f"═══════════════════════════════════")
        return "\n".join(lines)

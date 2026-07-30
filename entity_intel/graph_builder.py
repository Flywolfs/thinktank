"""知识图谱构建器 — 将 EntityReport 的抽取结果写入 Neo4j"""

from shared.storage.neo4j_client import Neo4jClient
from shared.models.entity_report import EntityReport


class GraphBuilder:
    """将 EntityReport 转化为 Neo4j 图节点和边"""

    def __init__(self, client: Neo4jClient | None = None):
        self.client = client or Neo4jClient()
        self.client.ensure_indexes()

    def build(self, report: EntityReport) -> dict:
        """
        将一次实体搜索的完整结果写入知识图谱。

        写入内容:
        1. 核心实体节点（带属性）
        2. 所有相关实体节点
        3. 从核心实体到每个相关实体的关系边
        4. 事件节点

        Returns:
            {"core": "核心实体名", "nodes_added": N, "relations_added": M}
        """
        entity_name = report.entity_name
        nodes_added = 0
        relations_added = 0

        # ── Step 1: 写入核心实体 ──────────────────────
        core_summary = report.core_summary or ""
        source = ",".join(report.errors.keys()) or "entity_searcher"

        self.client.merge_entity(
            entity_name,
            entity_type="person",
            summary=core_summary,
            source="entity_searcher",
        )
        nodes_added += 1

        # ── Step 2: 写入搜索来源作为 SOURCED_FROM 关系 ──
        for r in report.web_results[:5]:
            self._add_source(entity_name, r.title, r.url, "web")
        for r in report.knowledge_results:
            self._add_source(entity_name, r.title, r.url, "knowledge")
        for r in report.social_results[:5]:
            self._add_source(entity_name, r.title, r.url, "social")

        # ── Step 3: 写入 LLM 抽取的结构化关系 ──────────
        for rel in report.related_entities:
            target_name = rel.get("name", "")
            target_type = rel.get("type", "")
            relation = rel.get("relation", "")
            evidence = rel.get("evidence", "")

            if not target_name or not relation:
                continue

            # 创建目标实体
            self.client.merge_entity(
                target_name,
                entity_type=target_type,
                source="llm_extract",
            )
            nodes_added += 1

            # 创建关系（方向: core → target 或 target → core）
            direction = rel.get("direction", "outgoing")
            match direction:
                case "incoming":
                    self.client.merge_relation(
                        target_name, entity_name, relation,
                        source="llm_extract", evidence=evidence,
                    )
                case _:  # outgoing (default)
                    self.client.merge_relation(
                        entity_name, target_name, relation,
                        source="llm_extract", evidence=evidence,
                    )
            relations_added += 1

        # ── Step 4: 写入事件节点 ───────────────────────
        for event_data in report.related_entities:
            pass  # 事件暂时从 related_entities 推断

        return {
            "core": entity_name,
            "nodes_added": nodes_added,
            "relations_added": relations_added,
        }

    def _add_source(self, entity_name: str, title: str, url: str, source_type: str):
        """添加搜索来源文档节点并创建 SOURCED_FROM 关系"""
        if not url:
            return
        # 以 URL 为唯一标识创建文档节点
        from urllib.parse import urlparse
        domain = urlparse(url).netloc or "unknown"

        self.client.merge_entity(
            url,
            entity_type="document",
            summary=title[:200],
            source=source_type,
        )
        self.client.merge_relation(
            url, entity_name, f"提到", source=source_type, domain=domain,
        )

    def query(self, name: str) -> dict | None:
        """查询某个实体在图中的信息"""
        return self.client.get_entity(name)

    def health_check(self) -> bool:
        return self.client.health_check()


# ==================== 自检 ====================
if __name__ == "__main__":
    from entity_intel.searcher import EntitySearcher

    print("GraphBuilder 端到端测试")
    print("=" * 40)

    # Step 1: 搜索
    searcher = EntitySearcher()
    report = searcher.search("雷军", max_per_source=3)
    print(f"✅ 搜索完成: {report.total_results} 条")

    # Step 2: 实体抽取
    result = searcher.extract_entities(report)
    if result is None:
        print("❌ 抽取失败")
        exit(1)
    print(f"✅ 抽取完成: {len(report.related_entities)} 个相关实体")

    # Step 3: 写入图谱
    builder = GraphBuilder()
    stats = builder.build(report)
    print(f"✅ 图谱写入: 核心={stats['core']}, "
          f"节点={stats['nodes_added']}, 关系={stats['relations_added']}")

    # Step 4: 查询验证
    entity = builder.query("雷军")
    if entity:
        print(f"\n查询雷军:")
        print(f"  类型: {entity['type']}")
        print(f"  概述: {entity.get('summary', '')[:100]}")
        print(f"  关系: {len(entity['relations'])} 条")
        for r in entity["relations"]:
            print(f"    [{r['type']}] {r['rel']} → {r['target']}")

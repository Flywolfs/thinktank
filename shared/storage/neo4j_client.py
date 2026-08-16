"""Neo4j 知识图谱客户端"""

from neo4j import GraphDatabase, Driver

from shared.utils import config


def get_driver() -> Driver:
    """创建 Neo4j 驱动连接"""
    return GraphDatabase.driver(
        config.NEO4J_URI,
        auth=(config.NEO4J_USER, config.NEO4J_PASSWORD),
    )


class Neo4jClient:
    """知识图谱读写客户端"""

    def __init__(self, driver: Driver | None = None):
        self.driver = driver or get_driver()

    def health_check(self) -> bool:
        try:
            self.driver.verify_connectivity()
            return True
        except Exception:
            return False

    # ── 索引 ────────────────────────────────────────────

    def ensure_indexes(self):
        """确保必要索引存在"""
        queries = [
            "CREATE INDEX entity_name IF NOT EXISTS FOR (e:Entity) ON (e.name)",
            "CREATE INDEX entity_type IF NOT EXISTS FOR (e:Entity) ON (e.type)",
        ]
        with self.driver.session() as session:
            for q in queries:
                try:
                    session.run(q)
                except Exception:
                    pass  # 索引已存在

    # ── 实体 CRUD ───────────────────────────────────────

    def merge_entity(
        self,
        name: str,
        entity_type: str = "",
        summary: str = "",
        source: str = "",
        investigated: bool | None = None,
        aliases: list | None = None,
        **attrs,
    ) -> str:
        """
        创建或更新实体节点。同名实体合并，追加新属性。
        P4.2: investigated 标记 + aliases 别名列表（归一化用）。
        返回实体 name。
        """
        with self.driver.session() as session:
            result = session.run(
                """
                MERGE (e:Entity {name: $name})
                SET e.type = CASE WHEN $type <> '' THEN $type ELSE e.type END,
                    e.summary = CASE WHEN $summary <> '' THEN $summary ELSE e.summary END,
                    e.last_seen = datetime(),
                    e.sources = CASE
                        WHEN e.sources IS NULL THEN [$source]
                        WHEN $source IN e.sources THEN e.sources
                        ELSE e.sources + $source
                    END
                SET e += $attrs
                RETURN e.name AS name
                """,
                name=name,
                type=entity_type,
                summary=summary[:500],
                source=source,
                attrs=attrs,
            )
            record = result.single()
            return record["name"] if record else name

    def mark_investigated(self, name: str, count: int = 1) -> None:
        """P4.2: 标记实体已调查（investigated_count 递增）"""
        with self.driver.session() as session:
            session.run(
                """
                MERGE (e:Entity {name: $name})
                SET e.investigated = true,
                    e.investigated_count = coalesce(e.investigated_count, 0) + $count,
                    e.last_investigated_at = datetime()
                """,
                name=name,
                count=count,
            )

    def add_alias(self, key: str, alias: str) -> None:
        """P4.2: 别名增量学习 — 把新 name 加入实体 alias 列表"""
        if not key or not alias or alias == key:
            return
        with self.driver.session() as session:
            session.run(
                """
                MERGE (e:Entity {name: $key})
                SET e.aliases = CASE
                    WHEN e.aliases IS NULL THEN [$alias]
                    WHEN $alias IN e.aliases THEN e.aliases
                    ELSE e.aliases + $alias
                END
                """,
                key=key,
                alias=alias,
            )

    # ── 关系 CRUD ───────────────────────────────────────

    def merge_relation(
        self,
        from_entity: str,
        to_entity: str,
        relation: str,
        source: str = "",
        new_finding: str = "",
        **attrs,
    ):
        """
        创建或更新两个实体之间的关系。
        P4.2 证据累积: 已存在同向边 → 不新建，追加证据
        (evidence_count 递增, sources 追加, new_finding 覆盖为最近一次)。
        """
        rel_type = self._to_rel_type(relation)

        # 确保两端实体存在
        self.merge_entity(from_entity, source=source)
        self.merge_entity(to_entity, source=source)

        with self.driver.session() as session:
            # 查同向边是否存在
            existing = session.run(
                f"""
                MATCH (a:Entity {{name: $from_name}})
                MATCH (b:Entity {{name: $to_name}})
                MATCH (a)-[r:{rel_type}]->(b)
                RETURN r.relation AS relation, r.evidence_count AS evidence_count
                """,
                from_name=from_entity,
                to_name=to_entity,
            ).single()

            if existing:
                # 同向边已存在 → 证据累积（ON MATCH 语义）
                session.run(
                    f"""
                    MATCH (a:Entity {{name: $from_name}})
                    MATCH (b:Entity {{name: $to_name}})
                    MATCH (a)-[r:{rel_type}]->(b)
                    SET r.relation = $relation,
                        r.last_seen = datetime(),
                        r.evidence_count = coalesce(r.evidence_count, 1) + 1,
                        r.sources = CASE
                            WHEN r.sources IS NULL THEN [$source]
                            WHEN $source IN r.sources THEN r.sources
                            ELSE r.sources + $source
                        END
                    SET r += $attrs
                    """,
                    from_name=from_entity,
                    to_name=to_entity,
                    relation=relation,
                    source=source,
                    attrs=attrs,
                )
                # 更新 new_finding（单独写避免 attrs 冲突）
                if new_finding:
                    session.run(
                        f"""
                        MATCH (a:Entity {{name: $from_name}})-[r:{rel_type}]->(b:Entity {{name: $to_name}})
                        SET r.new_finding = $new_finding
                        """,
                        from_name=from_entity,
                        to_name=to_entity,
                        new_finding=new_finding[:300],
                    )
            else:
                # 新建边
                session.run(
                    f"""
                    MATCH (a:Entity {{name: $from_name}})
                    MATCH (b:Entity {{name: $to_name}})
                    MERGE (a)-[r:{rel_type}]->(b)
                    SET r.relation = $relation,
                        r.source = $source,
                        r.last_seen = datetime(),
                        r.evidence_count = 1,
                        r.sources = [$source]
                    SET r += $attrs
                    """,
                    from_name=from_entity,
                    to_name=to_entity,
                    relation=relation,
                    source=source,
                    attrs=attrs,
                )
                if new_finding:
                    session.run(
                        f"""
                        MATCH (a:Entity {{name: $from_name}})-[r:{rel_type}]->(b:Entity {{name: $to_name}})
                        SET r.new_finding = $new_finding
                        """,
                        from_name=from_entity,
                        to_name=to_entity,
                        new_finding=new_finding[:300],
                    )

    @staticmethod
    def _to_rel_type(relation_text: str) -> str:
        """将关系描述转为合法的 Cypher 关系类型（稳定且区分语义）

        P4.2 修复: 纯中文关系不再一律返回 RELATED_TO——
        用哈希生成稳定 rel_type（同文字→同类型，不同文字→不同类型），
        保证"朋友"和"资助方"是两条不同边（2.6 设计）。
        """
        import re
        import hashlib

        text = (relation_text or "").strip()
        if not text:
            return "RELATED_TO"
        # 英文优先（直接用英文作为类型）
        alpha = re.sub(r"[^a-zA-Z0-9]", "_", text)
        if len(alpha.strip("_")) > 1:
            rel = alpha.strip("_").upper()[:30]
            # Cypher 类型不能以数字开头 → 加 REL_ 前缀
            if rel[0].isdigit():
                rel = "REL_" + rel
            return rel
        # 纯中文/其他 → 哈希生成稳定类型（前缀 REL_ + 8位哈希）
        digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:8].upper()
        return f"REL_{digest}"

    # ── 查询 ────────────────────────────────────────────

    def get_entity(self, name: str) -> dict | None:
        """获取单个实体及其直接关系"""
        with self.driver.session() as session:
            result = session.run(
                """
                MATCH (e:Entity {name: $name})
                OPTIONAL MATCH (e)-[r]->(related)
                RETURN e, collect({type: type(r), rel: r.relation, target: related.name}) AS relations
                """,
                name=name,
            )
            record = result.single()
            if not record:
                return None
            node = record["e"]
            return {
                "name": node.get("name"),
                "type": node.get("type"),
                "summary": node.get("summary"),
                "relations": record["relations"],
            }

    def get_subgraph(self, name: str, depth: int = 1) -> list[dict]:
        """获取以某实体为中心的子图（N 跳范围内）"""
        # 变量长度路径不能参数化，用安全整数拼接（depth 来自 API 参数，限制范围）
        depth = max(1, min(int(depth), 5))
        with self.driver.session() as session:
            result = session.run(
                f"""
                MATCH path = (e:Entity {{name: $name}})-[*1..{depth}]-(related)
                UNWIND relationships(path) AS r
                UNWIND nodes(path) AS n
                RETURN DISTINCT
                    n.name AS name,
                    n.type AS type,
                    n.summary AS summary,
                    collect(DISTINCT {{type: type(r), rel: r.relation, other: 
                        CASE WHEN startNode(r).name = n.name THEN endNode(r).name ELSE startNode(r).name END
                    }}) AS relations
                """,
                name=name,
            )
            entities = {}
            for record in result:
                entities[record["name"]] = {
                    "name": record["name"],
                    "type": record["type"],
                    "summary": record["summary"],
                    "relations": record["relations"],
                }
            return list(entities.values())

    def entity_exists(self, name: str) -> bool:
        with self.driver.session() as session:
            result = session.run(
                "MATCH (e:Entity {name: $name}) RETURN count(e) AS c", name=name
            )
            return result.single()["c"] > 0


# ==================== 自检 ====================
if __name__ == "__main__":
    client = Neo4jClient()

    print("Neo4j 客户端自检")
    print("=" * 40)

    ok = client.health_check()
    print(f"  {'✅' if ok else '❌'} 连接")

    if not ok:
        exit(1)

    client.ensure_indexes()
    print(f"  ✅ 索引")

    # 写入测试
    client.merge_entity("测试实体", entity_type="test", summary="这是一个测试")
    client.merge_entity("小米集团", entity_type="organization", summary="智能硬件和电子产品公司")
    client.merge_relation("测试实体", "小米集团", "创始人", source="test")

    # 读取测试
    entity = client.get_entity("测试实体")
    print(f"  ✅ 写入+读取: {entity['name']} — {len(entity['relations'])} 条关系")
    for r in entity["relations"]:
        print(f"     {r['rel']} → {r['target']}")

    # 清理测试数据
    with client.driver.session() as session:
        session.run("MATCH (e:Entity {name: '测试实体'}) DETACH DELETE e")
        session.run("MATCH (e:Entity {name: '小米集团'}) DETACH DELETE e")
    print(f"  ✅ 清理")

"""P4.2 端到端验证（需 Neo4j 运行）：
1. 实体归一化入库（中文简体/英文/别名）→ 同名合并
2. 关系证据累积: A→B 两次 merge → evidence_count=2, sources 追加
3. investigated 标记 + 别名增量学习
4. 血统边: 父实体 → 子实体
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.storage.neo4j_client import Neo4jClient
from shared.nlp.entity_normalize import get_normalizer


def main():
    client = Neo4jClient()
    if not client.health_check():
        print("❌ Neo4j 未启动，请先 docker start neo4j")
        sys.exit(1)
    print("✅ Neo4j 连接正常")

    # 清理测试数据
    with client.driver.session() as s:
        s.run("MATCH (e:Entity) WHERE e.name IN ['测试甲', '测试乙', '台湾银行', '测试丙'] DETACH DELETE e")

    normalizer = get_normalizer()

    print("\n=== 1. 归一化入库（同名合并）===")
    key_a = normalizer.normalize("测试甲")
    client.merge_entity(key_a, entity_type="person", source="p42_test")
    # 第二次用别名/变体写入 → 归一化到同一 key
    alias_a = normalizer.normalize("测试甲(别名)")
    client.merge_entity(alias_a, entity_type="person", source="p42_test2")
    print(f"  normalize('测试甲') = {key_a!r}")
    print(f"  normalize('测试甲(别名)') = {alias_a!r}")
    assert key_a == alias_a, "括号内容应被去除，归一化到同一 key"
    # 查实体数量（应只有 1 个）
    with client.driver.session() as s:
        cnt = s.run("MATCH (e:Entity {name: $n}) RETURN count(e) AS c", n=key_a).single()["c"]
    print(f"  图谱中实体数: {cnt}（应为 1）")
    assert cnt == 1

    # 简体归一化
    tw = normalizer.normalize("臺灣銀行")
    client.merge_entity(tw, entity_type="organization", source="p42_test")
    print(f"  normalize('臺灣銀行') = {tw!r}（简体）")
    assert tw == "台湾银行"

    print("\n=== 2. 关系证据累积 ===")
    key_b = normalizer.normalize("测试乙")
    # 第一次
    client.merge_relation(key_a, key_b, "同代作家", source="job_1", new_finding="第一次发现")
    # 第二次（同向同 rel_type → 证据累积，不新建边）
    client.merge_relation(key_a, key_b, "同代作家", source="job_2", new_finding="第二次发现: 同窗证据")
    with client.driver.session() as s:
        row = s.run(
            "MATCH (a:Entity {name:$a})-[r]->(b:Entity {name:$b}) RETURN r.evidence_count AS ec, "
            "r.sources AS sources, r.new_finding AS nf",
            a=key_a, b=key_b,
        ).single()
    print(f"  evidence_count: {row['ec']}（应为 2）")
    print(f"  sources: {row['sources']}")
    print(f"  new_finding: {row['nf']}")
    assert row["ec"] == 2, "两次 merge 同向边应累积 evidence_count=2"
    assert len(row["sources"]) == 2, "sources 应累积 2 条"
    assert row["nf"] == "第二次发现: 同窗证据", "new_finding 应覆盖为最近一次"

    # 反向边 → 新边（B→A，不同方向）
    client.merge_relation(key_b, key_a, "被资助", source="job_3", new_finding="反向关系")
    with client.driver.session() as s:
        cnt2 = s.run(
            "MATCH (a:Entity {name:$a})-[r]->(b:Entity {name:$b}) RETURN count(r) AS c",
            a=key_a, b=key_b,
        ).single()["c"]
    print(f"  A→B 方向边数: {cnt2}（应为 1：同代作家；反向的被资助是 B→A）")
    assert cnt2 == 1, "A→B 方向应只有同代作家一条"

    # 同方向不同语义 → 两条边（2.6 核心: 朋友 vs 资助方 不合并）
    client.merge_relation(key_a, key_b, "资助方", source="job_4", new_finding="资金关系")
    with client.driver.session() as s:
        cnt3 = s.run(
            "MATCH (a:Entity {name:$a})-[r]->(b:Entity {name:$b}) RETURN count(r) AS c",
            a=key_a, b=key_b,
        ).single()["c"]
    print(f"  同方向不同语义边数: {cnt3}（应为 2：同代作家 + 资助方）")
    assert cnt3 == 2, "同方向不同语义关系应各自成边（2.6）"

    # 同方向同语义 → 证据累积（不新增边）
    client.merge_relation(key_a, key_b, "同代作家", source="job_5", new_finding="第三次验证")
    with client.driver.session() as s:
        cnt4 = s.run(
            "MATCH (a:Entity {name:$a})-[r]->(b:Entity {name:$b}) RETURN count(r) AS c",
            a=key_a, b=key_b,
        ).single()["c"]
        row = s.run(
            "MATCH (a:Entity {name:$a})-[r]->(b:Entity {name:$b}) WHERE r.relation = '同代作家' "
            "RETURN r.evidence_count AS ec", a=key_a, b=key_b,
        ).single()
    print(f"  同语义再 merge 后边数: {cnt4}（应仍为 2，不新增）")
    print(f"  同代作家 evidence_count: {row['ec']}（应为 3）")
    assert cnt4 == 2, "同语义 merge 不应新增边"
    assert row["ec"] == 3, "同语义 merge 应累积 evidence_count"

    print("\n=== 3. investigated 标记 + 血统边 ===")
    client.mark_investigated(key_a, count=1)
    client.mark_investigated(key_a, count=1)  # 第二次
    with client.driver.session() as s:
        row = s.run("MATCH (e:Entity {name:$n}) RETURN e.investigated AS inv, "
                    "e.investigated_count AS ic", n=key_a).single()
    print(f"  investigated: {row['inv']}, count: {row['ic']}（应为 2）")
    assert row["inv"] is True and row["ic"] == 2

    # 血统边（父实体 → 子实体）— 关系类型是哈希 REL_xxx，按 relation 属性查
    key_c = normalizer.normalize("测试丙")
    client.merge_relation(key_a, key_c, "调查关联", source="investigation",
                          new_finding="从父调查 job_x 深挖而来 (图谱关联)")
    with client.driver.session() as s:
        cnt3 = s.run(
            "MATCH (a:Entity {name:$a})-[r]->(c:Entity {name:$c}) "
            "WHERE r.relation = '调查关联' RETURN count(r) AS c",
            a=key_a, c=key_c,
        ).single()["c"]
    print(f"  血统边(调查关联): {cnt3}（应为 1）")
    assert cnt3 == 1

    # 清理
    with client.driver.session() as s:
        s.run("MATCH (e:Entity) WHERE e.name IN ['测试甲', '测试乙', '台湾银行', '测试丙'] DETACH DELETE e")
    print("\n✅ P4.2 端到端验证全部通过（测试数据已清理）")


if __name__ == "__main__":
    main()

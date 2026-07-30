"""实体抽取 — 从原始文本中提取人物、组织、事件及其关系"""

import json
from dataclasses import dataclass, field
from shared.llm.client import LLMClient

# ── 抽取 Prompt ───────────────────────────────────────


EXTRACT_PROMPT = """你是一个情报分析师，擅长从文本中提取实体及其关系。

给定一段关于某个核心实体的文本，请提取：

1. 核心实体的属性（如果文本包含）:
   - 名称、类型(person/organization/event/product)
   - 国籍/地点
   - 职位/角色
   - 关键日期
   - 一句话概述

2. 相关实体和关系:
   对于每个相关实体，说明：
   - name: 实体名称
   - type: person/organization/event/location/product
   - relation: 与核心实体的关系（如"创始人""CEO""竞争对手""投资了""位于"等）
   - evidence: 从原文中摘录的支持这句话的证据片段
   - direction: "outgoing"（核心实体→相关实体）或 "incoming"（相关实体→核心实体）

3. 关键事件:
   - date: 日期（YYYY-MM-DD 或 YYYY）
   - description: 事件描述
   - related_entities: 涉及的其他实体名称列表

请以 JSON 格式返回，结构如下：
{
  "core_entity": {
    "name": "核心实体名称",
    "type": "person/organization/event/product",
    "attributes": {},
    "summary": "一句话概述"
  },
  "related_entities": [
    {
      "name": "实体名称",
      "type": "person/organization/event/location/product",
      "relation": "关系描述",
      "evidence": "原文证据",
      "direction": "outgoing/incoming"
    }
  ],
  "events": [
    {
      "date": "YYYY-MM-DD",
      "description": "事件描述",
      "related_entities": ["相关实体名"]
    }
  ]
}

注意：
- 只提取文本中明确提到的实体和关系，不要推测
- 如果某个字段在原文中找不到，返回空字符串或空列表
- 相关实体去重，同一实体只出现一次
- 如果文本过长，聚焦于与核心实体最相关的部分"""


@dataclass
class ExtractedEntity:
    """LLM 抽取出的实体和关系"""
    name: str
    type: str  # person/organization/event/location/product
    relation: str = ""
    evidence: str = ""
    direction: str = "outgoing"

    def to_graph_tuple(self) -> tuple:
        """转为知识图谱三元组 (from, relation, to)"""
        return (self.from_entity or "", self.relation, self.name)


@dataclass
class ExtractionResult:
    """一次实体抽取的完整结果"""
    core_entity: dict = field(default_factory=dict)
    related_entities: list[ExtractedEntity] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    raw_json: dict = field(default_factory=dict)


class EntityExtractor:
    """实体抽取器 — 将非结构化文本转为结构化实体关系"""

    def __init__(self, llm: LLMClient | None = None):
        self.llm = llm or LLMClient()

    def extract(self, text: str, entity_name: str = "") -> ExtractionResult:
        """从一段文本中抽取实体和关系"""
        user_input = f"核心实体: {entity_name}\n\n文本:\n{text[:4000]}"

        try:
            data = self.llm.extract_json(EXTRACT_PROMPT, user_input)
        except (json.JSONDecodeError, Exception) as e:
            return ExtractionResult(raw_json={"error": str(e)})

        core = data.get("core_entity", {})
        related = [
            ExtractedEntity(
                name=r.get("name", ""),
                type=r.get("type", ""),
                relation=r.get("relation", ""),
                evidence=r.get("evidence", ""),
                direction=r.get("direction", "outgoing"),
            )
            for r in data.get("related_entities", [])
            if r.get("name")
        ]
        events = data.get("events", [])

        return ExtractionResult(
            core_entity=core,
            related_entities=related,
            events=events,
            raw_json=data,
        )

    def extract_from_report(self, report) -> dict:
        """
        从 EntityReport 的所有文本内容中抽取实体关系。
        """

        # 收集所有有内容的文本
        texts = []

        # 百科摘要（最结构化，权重最高）
        if report.core_summary:
            texts.append(report.core_summary)

        # 网页搜索摘要
        for r in report.web_results:
            if r.content:
                texts.append(f"[{r.title}] {r.content}")

        # B站视频标题
        for r in report.social_results:
            texts.append(f"[B站:{r.title}] UP:{r.author}")

        if not texts:
            return {"error": "没有可抽取的文本内容"}

        # 合并文本，限制总长度
        combined = "\n\n---\n\n".join(texts)
        result = self.extract(combined, report.entity_name)

        # 更新 report 的 related_entities
        report.related_entities = [
            {
                "name": re.name,
                "type": re.type,
                "relation": re.relation,
                "evidence": re.evidence[:100],
                "direction": re.direction,
            }
            for re in result.related_entities
        ]
        report.core_summary = result.core_entity.get("summary", report.core_summary)

        return result.raw_json


# ==================== 自检 ====================
if __name__ == "__main__":
    from entity_intel.searcher import EntitySearcher

    print("EntityExtractor 自检")
    print("=" * 40)

    # 1. 搜索
    searcher = EntitySearcher()
    if not searcher.available_sources():
        print("❌ 没有可用数据源")
        exit(1)

    report = searcher.search("雷军", max_per_source=3)
    print(f"搜索完成: {report.total_results} 条")

    # 2. 抽取
    extractor = EntityExtractor()

    try:
        result = extractor.extract_from_report(report)
        print(f"\n核心实体: {result.get('core_entity', {}).get('name', '?')}")
        print(f"类型: {result.get('core_entity', {}).get('type', '?')}")
        print(f"概述: {result.get('core_entity', {}).get('summary', '?')[:200]}")

        related = result.get("related_entities", [])
        print(f"\n相关实体 ({len(related)} 个):")
        for r in related:
            print(f"  [{r['type']}] {r['name']} — {r['relation']}")
            print(f"    证据: {r.get('evidence', '')[:80]}...")

        events = result.get("events", [])
        if events:
            print(f"\n关键事件 ({len(events)} 个):")
            for e in events:
                print(f"  {e.get('date', '?')}: {e.get('description', '')[:100]}")

    except Exception as e:
        print(f"❌ 抽取失败: {e}")

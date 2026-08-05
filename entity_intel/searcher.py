"""实体情报搜索编排器 — 并行调用多数据源，聚合为 EntityReport"""

import concurrent.futures
from datetime import datetime

from shared.crawlers.base import SearchParams, SearchResult, BaseProvider
from shared.crawlers.web_search import SerperProvider
from shared.crawlers.bilibili import BilibiliCLIProvider
from shared.crawlers.knowledge import WikipediaProvider
from shared.models.entity_report import EntityReport
from shared.utils.logger import archive_search_results, get_current_logger


class EntitySearcher:
    """
    实体情报搜索编排器

    输入: 实体名 + 时间范围 + 搜索深度
    输出: EntityReport — 聚合了知识库/网页/社交媒体/企业信息的多源结果

    用法:
        searcher = EntitySearcher()
        report = searcher.search("雷军", max_depth=1)
        print(report.print_summary())
    """

    def __init__(self):
        self.providers: dict[str, BaseProvider] = {}

    def _get_providers(self):
        """惰性初始化 Provider（避免导入时就初始化外部依赖）"""
        if self.providers:
            return self.providers

        self.providers = {}

        # 知识库
        try:
            wiki = WikipediaProvider()
            if wiki.health_check():
                self.providers["wikipedia"] = wiki
        except Exception:
            pass

        # 搜索引擎
        try:
            serper = SerperProvider()
            if serper.health_check():
                self.providers["serper"] = serper
        except Exception:
            pass

        # B站
        try:
            bili = BilibiliCLIProvider()
            if bili.health_check():
                self.providers["bilibili"] = bili
        except Exception:
            pass

        # 企业信息（香港可用方案）
        try:
            from shared.crawlers.enterprise import ChinaEnterpriseProvider
            ent = ChinaEnterpriseProvider()
            if ent.health_check():
                self.providers["enterprise"] = ent
        except Exception:
            pass

        # 知乎 / 小红书 / 微博（MediaCrawler）
        try:
            from shared.crawlers.media_crawler import (
                ZhihuSearchProvider, XHSSearchProvider, WeiboSearchProvider,
            )
            for name, cls in (
                ("zhihu", ZhihuSearchProvider),
                ("xiaohongshu", XHSSearchProvider),
                ("weibo", WeiboSearchProvider),
            ):
                try:
                    p = cls()
                    if p.health_check():
                        self.providers[name] = p
                except Exception:
                    continue
        except Exception:
            pass

        return self.providers

    def search(
        self,
        entity_name: str,
        time_start: datetime | None = None,
        time_end: datetime | None = None,
        max_depth: int = 1,
        max_per_source: int = 10,
    ) -> EntityReport:
        """
        并行搜索所有可用数据源

        Args:
            entity_name: 实体名称（人名/公司名/事件名）
            time_start: 时间范围起始
            time_end: 时间范围结束（默认今天）
            max_depth: 搜索深度（暂未实现递归，预留给 Phase 1）
            max_per_source: 每个源最多返回条数
        """
        report = EntityReport(
            entity_name=entity_name,
            search_depth=max_depth,
        )

        providers = self._get_providers()
        if not providers:
            report.errors["_all"] = "没有可用的数据源"
            return report

        params = SearchParams(
            query=entity_name,
            time_start=time_start,
            time_end=time_end,
            max_results=max_per_source,
        )

        # 并行调用所有 Provider
        results: dict[str, list[SearchResult]] = {}
        errors: dict[str, str] = {}
        logger = get_current_logger()

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            future_map = {
                executor.submit(self._safe_search, name, provider, params): name
                for name, provider in providers.items()
            }
            for future in concurrent.futures.as_completed(future_map):
                name = future_map[future]
                try:
                    items = future.result()
                    results[name] = items
                    logger.provider_result(
                        name, entity_name, count=len(items),
                    )
                except Exception as e:
                    errors[name] = str(e)
                    logger.provider_result(name, entity_name, count=0, error=str(e)[:200])

        report.errors = errors

        # 归类结果
        for source_name, items in results.items():
            for item in items:
                if item.source_type == "knowledge":
                    report.knowledge_results.append(item)
                elif item.source_type == "social":
                    report.social_results.append(item)
                else:
                    report.web_results.append(item)

        report.total_results = sum(len(v) for v in results.values())

        # 统一存档：所有源结果落盘 data/raw/search/
        all_results = (
            report.web_results + report.social_results + report.knowledge_results
        )
        archive_search_results(all_results, entity_name, source="search")

        # 提取百科摘要作为 core_summary
        self._extract_core_summary(report)

        return report

    def search_fast(
        self,
        entity_name: str,
        max_per_source: int = 10,
    ) -> EntityReport:
        """快速搜索：跳过 MediaCrawler（zhihu/xhs/weibo 慢源）。
        用于一轮中的后续 query——同一实体不同关键词，
        MediaCrawler 结果高度重叠，重复爬取浪费且会互相干扰。"""
        report = EntityReport(entity_name=entity_name, search_depth=1)
        providers = self._get_providers()
        if not providers:
            return report

        params = SearchParams(query=entity_name, max_results=max_per_source)
        results: dict[str, list[SearchResult]] = {}
        errors: dict[str, str] = {}
        logger = get_current_logger()

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            future_map = {
                executor.submit(self._safe_search, name, provider, params): name
                for name, provider in providers.items()
                if name not in ("zhihu", "xiaohongshu", "weibo")
            }
            for future in concurrent.futures.as_completed(future_map):
                name = future_map[future]
                try:
                    items = future.result()
                    results[name] = items
                    logger.provider_result(name, entity_name, count=len(items))
                except Exception as e:
                    errors[name] = str(e)
                    logger.provider_result(name, entity_name, count=0, error=str(e)[:200])

        report.errors = errors
        for source_name, items in results.items():
            for item in items:
                if item.source_type == "knowledge":
                    report.knowledge_results.append(item)
                elif item.source_type == "social":
                    report.social_results.append(item)
                else:
                    report.web_results.append(item)
        report.total_results = sum(len(v) for v in results.values())

        # 统一存档（快速搜索同样存档）
        all_results = (
            report.web_results + report.social_results + report.knowledge_results
        )
        archive_search_results(all_results, entity_name, source="search_fast")
        return report

    def extract_entities(self, report: EntityReport) -> dict | None:
        """对搜索结果运行 LLM 实体抽取，填充 report.related_entities"""
        try:
            from shared.llm.extractor import EntityExtractor
            extractor = EntityExtractor()
            return extractor.extract_from_report(report)
        except ValueError as e:
            report.errors["llm_extract"] = str(e)
            return None
        except Exception as e:
            report.errors["llm_extract"] = f"抽取失败: {e}"
            return None

    def filter_results(
        self, report: EntityReport, hints: str = "", goal: str = "", use_llm: bool = True
    ) -> EntityReport:
        """对搜索结果执行相关性筛选（复用通用 shared/nlp/relevance），原地更新 report"""
        try:
            from entity_intel.relevance import filter_entity_report
            return filter_entity_report(report, hints=hints, goal=goal, use_llm=use_llm)
        except Exception as e:
            report.errors["relevance_filter"] = f"筛选失败: {e}"
            return report

    def synthesize(
        self,
        report: EntityReport,
        hints: str = "",
        goal: str = "",
    ):
        """
        信息整合推理：把筛选后的元数据 + 抽取的实体整合为完整逻辑链路和报告。

        这是进入知识图谱之前的最后一步——报告先给用户审阅（HITL），
        用户决定是否构建知识图谱。
        """
        try:
            from entity_intel.synthesizer import Synthesizer
            from shared.models.analysis_report import AnalysisReport
            return Synthesizer().synthesize(report, hints=hints, goal=goal)
        except Exception as e:
            from shared.models.analysis_report import AnalysisReport
            report.errors["synthesize"] = f"报告生成失败: {e}"
            return AnalysisReport(entity_name=report.entity_name)

    def full_pipeline(
        self,
        entity_name: str,
        hints: str = "",
        goal: str = "",
        max_per_source: int = 10,
        use_llm_filter: bool = True,
    ) -> dict:
        """
        完整流水线: 搜索 → 筛选 → 实体抽取 → 信息整合推理 → 报告

        返回 dict:
        {
            "report": EntityReport,      # 搜索+筛选+抽取结果
            "analysis": AnalysisReport,  # 整合推理报告（给用户审阅）
        }
        用户审阅 analysis 后，决定是否构建知识图谱。
        """
        report = self.search(
            entity_name, max_per_source=max_per_source
        )
        self.filter_results(report, hints=hints, goal=goal, use_llm=use_llm_filter)
        self.extract_entities(report)
        analysis = self.synthesize(report, hints=hints, goal=goal)
        return {"report": report, "analysis": analysis}

    @staticmethod
    def _safe_search(name: str, provider: BaseProvider, params: SearchParams) -> list[SearchResult]:
        """执行搜索，出错时向上抛（由调用方捕获并记录到 errors）"""
        return provider.search(params)

    def _extract_core_summary(self, report: EntityReport):
        """从知识库结果中提取实体概述"""
        for r in report.knowledge_results:
            if r.source == "wikipedia" and r.content:
                report.core_summary = r.content
                return
        # 如果没有百科，用搜索引擎 Knowledge Graph
        for r in report.web_results:
            if r.source_type == "knowledge" and r.content:
                report.core_summary = r.content
                return

    def available_sources(self) -> list[str]:
        """列出当前可用的数据源"""
        providers = self._get_providers()
        return sorted(providers.keys())


# ==================== 自检 ====================
if __name__ == "__main__":
    import sys

    searcher = EntitySearcher()
    sources = searcher.available_sources()

    print(f"可用数据源: {sources}")
    if not sources:
        print("❌ 没有可用数据源，退出")
        sys.exit(1)

    print()

    # 测试 1: 基础搜索
    test_entities = ["雷军"]

    for entity in test_entities:
        print(f"搜索: {entity}")
        print("-" * 40)

        report = searcher.search(entity, max_per_source=5)
        print(report.print_summary())
        print()

    # 测试 2: 时间过滤
    print(f"搜索: 雷军 (2024年至今)")
    print("-" * 40)
    report2 = searcher.search(
        "雷军",
        time_start=datetime(2024, 1, 1),
        max_per_source=5,
    )
    print(f"  web: {len(report2.web_results)} 条")
    print(f"  social: {len(report2.social_results)} 条")
    print(f"  knowledge: {len(report2.knowledge_results)} 条")

"""知识库数据源: Wikipedia + Wikidata + 百度百科"""

from datetime import datetime
import httpx

from shared.crawlers.base import BaseProvider, SearchParams, SearchResult


# Wikipedia REST API 要求 User-Agent
HEADERS = {
    "User-Agent": "ThinkTank/0.1 (research project; https://github.com)"
}


class WikipediaProvider(BaseProvider):
    """Wikipedia 知识库 — 通过 REST API 获取实体摘要"""

    SOURCE = "wikipedia"
    SOURCE_TYPE = "knowledge"
    REST_API = "https://{lang}.wikipedia.org/api/rest_v1/page/summary/{title}"

    def search(self, params: SearchParams) -> list[SearchResult]:
        """搜索 Wikipedia 页面。time_start/time_end 被忽略（百科无时间维度）"""
        results = []

        # 先试中文，再试英文
        for lang in ("zh", "en"):
            url = self.REST_API.format(lang=lang, title=params.query)
            try:
                r = httpx.get(url, headers=HEADERS, timeout=10)
                if r.status_code == 200:
                    data = r.json()
                    results.append(self._parse(data, lang))
                    break
                elif r.status_code == 404:
                    continue
            except httpx.RequestError:
                continue

        return results

    def _parse(self, data: dict, lang: str) -> SearchResult:
        return SearchResult(
            source=self.SOURCE,
            source_type=self.SOURCE_TYPE,
            url=data.get("content_urls", {}).get("desktop", {}).get("page", ""),
            title=data.get("title", ""),
            content=data.get("extract", "")[:2000],
            author="Wikipedia",
            published_at=datetime.fromisoformat(
                data.get("timestamp", "").replace("Z", "+00:00")
            ) if data.get("timestamp") else None,
            metadata={
                "lang": lang,
                "page_id": data.get("pageid"),
                "description": data.get("description", ""),
                "thumbnail": data.get("thumbnail", {}).get("source", ""),
            }
        )

    def health_check(self) -> bool:
        try:
            url = self.REST_API.format(lang="en", title="Computer")
            r = httpx.get(url, headers=HEADERS, timeout=5)
            return r.status_code == 200
        except httpx.RequestError:
            return False


class WikidataProvider(BaseProvider):
    """Wikidata 结构化知识库 — SPARQL 查询获取实体关系"""

    SOURCE = "wikidata"
    SOURCE_TYPE = "knowledge"
    ENDPOINT = "https://query.wikidata.org/sparql"

    def search(self, params: SearchParams) -> list[SearchResult]:
        # Phase 1 实现：需要通过 Wikipedia 拿到 QID，再 SPARQL 查询
        return []

    def health_check(self) -> bool:
        try:
            r = httpx.get(
                self.ENDPOINT,
                params={
                    "query": "SELECT ?x WHERE {?x ?y ?z} LIMIT 1",
                    "format": "json",
                },
                headers=HEADERS,
                timeout=10,
            )
            return r.status_code == 200
        except httpx.RequestError:
            return False


class BaiduBaikeProvider(BaseProvider):
    """百度百科 — 通过 bb-browser 爬取"""

    SOURCE = "baidu_baike"
    SOURCE_TYPE = "knowledge"

    def search(self, params: SearchParams) -> list[SearchResult]:
        # Phase 1 实现：通过 bb-browser 打开百科页面，解析摘要和 Infobox
        return []

    def health_check(self) -> bool:
        return True


# ==================== 自检 ====================
if __name__ == "__main__":
    providers = [
        ("Wikipedia (en)", WikipediaProvider()),
    ]

    for name, provider in providers:
        print(f"\n{'='*50}")
        print(f"  测试: {name}")
        print(f"{'='*50}")

        # Step 1: 健康检查
        ok = provider.health_check()
        status = "✅" if ok else "❌"
        print(f"  {status} health_check(): {ok}")

        if not ok:
            continue

        # Step 2: 搜索测试 — 中文
        for query in ["雷军", "Steve Jobs"]:
            params = SearchParams(query=query, max_results=1)
            results = provider.search(params)
            if results:
                r = results[0]
                print(f"  ✅ '{query}' → {r.title}")
                print(f"     {' '.join(r.content[:120].split())}...")
                print(f"     {r.url}")
            else:
                print(f"  ❌ '{query}' → 无结果")

"""搜索引擎 Provider: Serper (Google) + SerpApi (Baidu) + Bing (免费备用)"""

from datetime import datetime
import httpx

from shared.crawlers.base import BaseProvider, SearchParams, SearchResult
from shared.utils import config

HEADERS = {
    "User-Agent": "ThinkTank/0.1 (research project; https://github.com)"
}


class SerperProvider(BaseProvider):
    """Serper.dev — Google 搜索，最快最便宜的 SERP API"""

    SOURCE = "serper"
    SOURCE_TYPE = "search"
    API_URL = "https://google.serper.dev/search"

    def search(self, params: SearchParams) -> list[SearchResult]:
        api_key = config.SERPER_API_KEY
        if not api_key:
            raise ValueError(
                "SERPER_API_KEY 未设置。请在 .env 文件中配置。\n"
                "获取: https://serper.dev"
            )

        payload = {
            "q": params.query,
            "num": min(params.max_results, 100),
            "gl": "cn",
            "hl": "zh-cn",
        }

        search_query = params.query
        if params.time_start or params.time_end:
            search_query = self._add_time_restrict(
                search_query, params.time_start, params.time_end
            )
            payload["q"] = search_query

        try:
            r = httpx.post(
                self.API_URL,
                json=payload,
                headers={
                    **HEADERS,
                    "X-API-KEY": api_key,
                    "Content-Type": "application/json",
                },
                timeout=15,
            )
            r.raise_for_status()
            data = r.json()
        except httpx.RequestError as e:
            raise ConnectionError(f"Serper API 请求失败: {e}")

        return self._parse(data)

    def _parse(self, data: dict) -> list[SearchResult]:
        results = []

        kg = data.get("knowledgeGraph", {})
        if kg:
            results.append(SearchResult(
                source=self.SOURCE,
                source_type="knowledge",
                url=kg.get("descriptionLink", ""),
                title=kg.get("title", ""),
                content=kg.get("description", ""),
                metadata={"kg_type": kg.get("type", ""), "attributes": kg.get("attributes", {})},
            ))

        for item in data.get("organic", []):
            results.append(SearchResult(
                source=self.SOURCE,
                source_type=self.SOURCE_TYPE,
                url=item.get("link", ""),
                title=item.get("title", ""),
                content=item.get("snippet", ""),
                metadata={
                    "position": item.get("position"),
                    "domain": item.get("displayedLink", ""),
                },
            ))
        return results

    def _add_time_restrict(
        self, query: str, start: datetime | None, end: datetime | None
    ) -> str:
        parts = [query]
        if start:
            parts.append(f"after:{start.strftime('%Y-%m-%d')}")
        if end:
            parts.append(f"before:{end.strftime('%Y-%m-%d')}")
        return " ".join(parts)

    def supports_time_filter(self) -> bool:
        return True

    def health_check(self) -> bool:
        api_key = config.SERPER_API_KEY
        if not api_key:
            print("  ⚠️ SERPER_API_KEY 未设置，跳过")
            return False
        try:
            r = httpx.post(
                self.API_URL,
                json={"q": "test"},
                headers={"X-API-KEY": api_key, **HEADERS},
                timeout=5,
            )
            return r.status_code == 200
        except httpx.RequestError:
            return False


class SerpApiBaiduProvider(BaseProvider):
    """SerpApi — 百度搜索（少数支持百度的 SERP API）"""

    SOURCE = "serpapi_baidu"
    SOURCE_TYPE = "search"
    API_URL = "https://serpapi.com/search"

    def search(self, params: SearchParams) -> list[SearchResult]:
        api_key = config.SERPAPI_API_KEY
        if not api_key:
            raise ValueError(
                "SERPAPI_API_KEY 未设置。请在 .env 文件中配置。\n"
                "获取: https://serpapi.com"
            )

        query_params = {
            "api_key": api_key,
            "engine": "baidu",
            "q": params.query,
            "num": min(params.max_results, 20),
        }

        try:
            r = httpx.get(self.API_URL, params=query_params, headers=HEADERS, timeout=15)
            r.raise_for_status()
            data = r.json()
        except httpx.RequestError as e:
            raise ConnectionError(f"SerpApi 请求失败: {e}")

        return self._parse(data)

    def _parse(self, data: dict) -> list[SearchResult]:
        results = []
        for item in data.get("organic_results", []):
            results.append(SearchResult(
                source=self.SOURCE,
                source_type=self.SOURCE_TYPE,
                url=item.get("link", ""),
                title=item.get("title", ""),
                content=item.get("snippet", ""),
                metadata={
                    "position": item.get("position"),
                    "domain": item.get("displayed_link", ""),
                },
            ))
        return results

    def supports_time_filter(self) -> bool:
        return True

    def health_check(self) -> bool:
        api_key = config.SERPAPI_API_KEY
        if not api_key:
            print("  ⚠️ SERPAPI_API_KEY 未设置，跳过")
            return False
        try:
            r = httpx.get(
                self.API_URL,
                params={"api_key": api_key, "engine": "baidu", "q": "test"},
                timeout=10,
            )
            return r.status_code == 200
        except httpx.RequestError:
            return False


class BingSearchProvider(BaseProvider):
    """Bing Search API — 免费备用方案"""

    SOURCE = "bing"
    SOURCE_TYPE = "search"
    API_URL = "https://api.bing.microsoft.com/v7.0/search"

    def search(self, params: SearchParams) -> list[SearchResult]:
        api_key = config.BING_API_KEY
        if not api_key:
            raise ValueError("BING_API_KEY 未设置。请在 .env 文件中配置。")

        query_params = {
            "q": params.query,
            "count": min(params.max_results, 50),
            "mkt": "zh-CN",
        }

        try:
            r = httpx.get(
                self.API_URL,
                params=query_params,
                headers={"Ocp-Apim-Subscription-Key": api_key, **HEADERS},
                timeout=15,
            )
            r.raise_for_status()
            data = r.json()
        except httpx.RequestError as e:
            raise ConnectionError(f"Bing API 请求失败: {e}")

        return self._parse(data)

    def _parse(self, data: dict) -> list[SearchResult]:
        results = []
        for item in data.get("webPages", {}).get("value", []):
            results.append(SearchResult(
                source=self.SOURCE,
                source_type=self.SOURCE_TYPE,
                url=item.get("url", ""),
                title=item.get("name", ""),
                content=item.get("snippet", ""),
                published_at=datetime.fromisoformat(
                    item["dateLastCrawled"].replace("Z", "+00:00")
                ) if item.get("dateLastCrawled") else None,
            ))
        return results

    def supports_time_filter(self) -> bool:
        return True

    def health_check(self) -> bool:
        api_key = config.BING_API_KEY
        if not api_key:
            print("  ⚠️ BING_API_KEY 未设置，跳过")
            return False
        try:
            r = httpx.get(
                self.API_URL,
                params={"q": "test", "count": 1, "mkt": "zh-CN"},
                headers={"Ocp-Apim-Subscription-Key": api_key, **HEADERS},
                timeout=5,
            )
            return r.status_code == 200
        except httpx.RequestError:
            return False


# ==================== 自检 ====================
if __name__ == "__main__":
    providers = [
        ("Serper (Google)", SerperProvider()),
        ("SerpApi (Baidu)", SerpApiBaiduProvider()),
        ("Bing (免费备用)", BingSearchProvider()),
    ]

    for name, provider in providers:
        print(f"\n{'='*50}")
        print(f"  测试: {name}")
        print(f"{'='*50}")

        ok = provider.health_check()
        status = "✅" if ok else "⚠️"
        print(f"  {status} health_check(): {ok}")

        if not ok:
            print(f"  → 跳过（缺少 API Key 或网络不可达）")
            continue

        for query in ["雷军", "字节跳动"]:
            params = SearchParams(query=query, max_results=3)
            try:
                results = provider.search(params)
                print(f"  ✅ '{query}' → {len(results)} 条")
                for r in results[:3]:
                    print(f"     [{r.metadata.get('position', '?')}] {r.title[:60]}")
            except Exception as e:
                print(f"  ❌ '{query}' → {e}")

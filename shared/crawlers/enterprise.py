"""中国企业信息 Provider — 在香港地区访问受限情况下的替代方案

策略:
1. 从 Serper Google 搜索结果提取天眼查/企查查/爱企查的摘要片段
   (Google 已经索引了这些站的工商信息，即使直接访问被墙)
2. 直连 cnipa.gov.cn 查询商标/专利
3. 用 LLM 从搜索结果片段中解析企业结构化信息

注意: 这些数据来自搜索引擎的公开索引，时效性比直接访问天眼查差，
但作为实体情报的补充信号已经足够 — 我们需要的是实体关系线索，
不是 100% 精确的工商档案。"""

import re
import httpx
from shared.crawlers.base import BaseProvider, SearchParams, SearchResult
from shared.crawlers.web_search import SerperProvider

HEADERS = {
    "User-Agent": "ThinkTank/0.1 (research project; https://github.com)"
}

# 企业信息在搜索结果中的常见模式
ENTERPRISE_PATTERNS = [
    r"(?:法定代表人|法人)[:：]\s*(?P<legal>[^\s,，;；]+)",
    r"注册资本[:：]\s*(?P<capital>[^\s,，;；]+)",
    r"成立(?:日期|时间)[:：]\s*(?P<date>\d{4}[-年]\d{1,2}[-月]\d{1,2}[日]?)",
    r"统一社会信用代码[:：]\s*(?P<credit_code>[A-Za-z0-9]{18})",
    r"经营状态[:：]\s*(?P<status>存续|在业|注销|吊销|迁出)",
    r"(?:地址|住所)[:：]\s*(?P<address>[^,，;；]{5,50})",
]


class ChinaEnterpriseProvider(BaseProvider):
    """
    中国企业信息采集 — 香港可用方案

    核心思路：Google 搜索结果的摘要片段里已经包含工商关键信息。
    不需要直接访问天眼查/企查查，从搜索引擎缓存里提取即可。
    """

    SOURCE = "china_enterprise"
    SOURCE_TYPE = "enterprise"

    def __init__(self):
        self.serper = SerperProvider()

    def search(self, params: SearchParams) -> list[SearchResult]:
        query = params.query

        # 先判断是否像公司名（含"公司""有限""集团"等）
        if not self._is_likely_enterprise(query):
            return []

        results = []

        # 从搜索引擎摘录中提取
        search_results = self._search_snippets(query, params.max_results)
        results.extend(search_results)

        # 从 cnipa.gov.cn 直连搜商标
        trademark_results = self._search_trademark(query)
        results.extend(trademark_results)

        return results

    def _is_likely_enterprise(self, name: str) -> bool:
        """是否可能为企业名。宽松匹配，因为很多公司简称不含'公司'（如'字节跳动'）"""
        # 包含企业关键词 → 确定
        keywords = ["公司", "有限", "集团", "科技", "企业", "股份", "合伙",
                    "银行", "保险", "证券", "基金"]
        if any(k in name for k in keywords):
            return True
        # 纯简称 → 可能，用搜索引擎验证
        return len(name) >= 3  # 至少3个字，让搜索引擎去判断

    def _search_snippets(self, company_name: str, max_results: int) -> list[SearchResult]:
        """从 Google 搜索结果摘录中提取企业信息"""
        # 搜索企业名 + 工商信息关键词
        queries = [
            f"{company_name} 法定代表人 注册资本",
            f"{company_name} 统一社会信用代码",
            f"{company_name} 工商信息",
        ]

        all_snippets = []
        seen_urls = set()

        for q in queries:
            try:
                serp = self.serper.search(SearchParams(query=q, max_results=3))
                for r in serp:
                    if r.url not in seen_urls and r.content:
                        seen_urls.add(r.url)
                        all_snippets.append(r)
            except Exception:
                continue

        # 从每个 snippet 中提关键字段
        results = []
        for serp_result in all_snippets:
            extracted = self._parse_snippet(serp_result.content)

            # 判断来源域名
            domain = serp_result.metadata.get("domain", "")
            source_label = {
                "tianyancha.com": "天眼查",
                "qcc.com": "企查查",
                "aiqicha.baidu.com": "爱企查",
                "gsxt.gov.cn": "国家企业信用信息公示系统",
            }.get(domain, "企业信息")

            if extracted.get("legal") or extracted.get("capital"):
                results.append(SearchResult(
                    source=f"{self.SOURCE}_{source_label}",
                    source_type=self.SOURCE_TYPE,
                    url=serp_result.url,
                    title=serp_result.title,
                    content=serp_result.content,
                    metadata={
                        **extracted,
                        "domain": domain,
                        "search_query": q,
                    },
                ))

        return results[:max_results]

    def _parse_snippet(self, snippet: str) -> dict:
        """从搜索结果片段中正则提取结构化字段"""
        extracted = {}
        for pattern in ENTERPRISE_PATTERNS:
            match = re.search(pattern, snippet)
            if match:
                # 使用第一个命名组
                for key, val in match.groupdict().items():
                    if val and key not in extracted:
                        extracted[key] = val.strip()
        return extracted

    def _search_trademark(self, company_name: str) -> list[SearchResult]:
        """从 cnipa.gov.cn 搜索商标信息（直连，香港可用）"""
        # cnipa 商标搜索的前端 API
        url = "https://sbj.cnipa.gov.cn/sbj/search/ssSearch"
        try:
            r = httpx.post(
                url,
                json={
                    "page": 1,
                    "pageSize": 5,
                    "searchType": "sqName",
                    "keyword": company_name,
                },
                headers={**HEADERS, "Content-Type": "application/json;charset=UTF-8"},
                timeout=10,
            )
            if r.status_code == 200:
                data = r.json()
                return self._parse_trademark_results(data, company_name)
        except Exception:
            pass
        return []

    def _parse_trademark_results(self, data: dict, company_name: str) -> list[SearchResult]:
        """解析 cnipa 商标搜索结果"""
        results = []
        records = data.get("rows", []) or data.get("data", {}).get("rows", [])
        for item in records[:5]:
            results.append(SearchResult(
                source=f"{self.SOURCE}_cnipa",
                source_type=self.SOURCE_TYPE,
                url=f"https://sbj.cnipa.gov.cn/sbj/search?keyword={company_name}",
                title=item.get("tmName", item.get("商标名称", "")),
                content=f"申请人: {item.get('applicantCn', item.get('申请人', ''))} | "
                        f"注册号: {item.get('regNo', item.get('注册号', ''))} | "
                        f"类别: {item.get('intCls', item.get('国际分类', ''))}",
                metadata={
                    "reg_no": item.get("regNo", item.get("注册号", "")),
                    "category": item.get("intCls", item.get("国际分类", "")),
                    "applicant": item.get("applicantCn", item.get("申请人", "")),
                },
            ))
        return results

    def health_check(self) -> bool:
        return self.serper.health_check()


# ==================== 自检 ====================
if __name__ == "__main__":
    provider = ChinaEnterpriseProvider()

    print("ChinaEnterpriseProvider 自检")
    print("=" * 40)
    print(f"  health_check: {'✅' if provider.health_check() else '❌'}")

    for company in ["小米科技", "字节跳动"]:
        print(f"\n  搜索: {company}")
        results = provider.search(SearchParams(query=company, max_results=5))
        if results:
            for r in results:
                meta = r.metadata
                fields = []
                if meta.get("legal"): fields.append(f"法人:{meta['legal']}")
                if meta.get("capital"): fields.append(f"资本:{meta['capital']}")
                if meta.get("status"): fields.append(f"状态:{meta['status']}")
                if meta.get("reg_no"): fields.append(f"商标:{meta['reg_no']}")
                print(f"    [{r.source}] {' | '.join(fields) if fields else r.title[:50]}")
        else:
            print(f"    (无企业数据)")

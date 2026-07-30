"""知乎搜索 + 小红书搜索 Provider

当前实现为骨架，核心搜索依赖 MediaCrawler。
MediaCrawler 需要 Playwright + 各平台登录态。
Phase 0 中可以先用 bb-browser 手动验证搜索可用性。
"""

from datetime import datetime

from shared.crawlers.base import BaseProvider, SearchParams, SearchResult


class ZhihuSearchProvider(BaseProvider):
    """知乎搜索 — 按实体名搜索问答/文章/专栏

    主通道: MediaCrawler (Playwright 浏览器自动化)
    备用:   RSSHub / bb-browser
    """

    SOURCE = "zhihu"
    SOURCE_TYPE = "social"

    def search(self, params: SearchParams) -> list[SearchResult]:
        """
        Phase 1 实现：
        1. 通过 MediaCrawler 调用知乎搜索
        2. 或用 bb-browser 打开 https://www.zhihu.com/search?q=xxx&type=content
        3. 解析搜索结果列表
        """
        return []

    def health_check(self) -> bool:
        """检查 MediaCrawler 或 RSSHub 是否可用"""
        return True  # Phase 1 实现


class ZhihuAnswerProvider(BaseProvider):
    """知乎回答正文抓取"""

    SOURCE = "zhihu_answer"
    SOURCE_TYPE = "social"

    def search(self, params: SearchParams) -> list[SearchResult]:
        return []  # Phase 1

    def health_check(self) -> bool:
        return True


class ZhihuArticleProvider(BaseProvider):
    """知乎文章正文抓取"""

    SOURCE = "zhihu_article"
    SOURCE_TYPE = "social"

    def search(self, params: SearchParams) -> list[SearchResult]:
        return []  # Phase 1

    def health_check(self) -> bool:
        return True


class ZhihuCommentProvider(BaseProvider):
    """知乎评论抓取"""

    SOURCE = "zhihu_comment"
    SOURCE_TYPE = "social"

    def search(self, params: SearchParams) -> list[SearchResult]:
        return []  # Phase 1

    def health_check(self) -> bool:
        return True


class XHSSearchProvider(BaseProvider):
    """小红书搜索 — 按实体名搜索笔记

    依赖: MediaCrawler + 小红书登录态
    需要先用 bb-browser 登录小红书一次，保存 Cookie
    """

    SOURCE = "xiaohongshu"
    SOURCE_TYPE = "social"

    def search(self, params: SearchParams) -> list[SearchResult]:
        """
        Phase 1 实现：
        MediaCrawler 小红书搜索
        """
        return []

    def health_check(self) -> bool:
        return True  # Phase 1


class XHSContentProvider(BaseProvider):
    """小红书笔记正文抓取"""

    SOURCE = "xiaohongshu_content"
    SOURCE_TYPE = "social"

    def search(self, params: SearchParams) -> list[SearchResult]:
        return []  # Phase 1

    def health_check(self) -> bool:
        return True


class XHSCommentProvider(BaseProvider):
    """小红书笔记评论"""

    SOURCE = "xiaohongshu_comment"
    SOURCE_TYPE = "social"

    def search(self, params: SearchParams) -> list[SearchResult]:
        return []  # Phase 1

    def health_check(self) -> bool:
        return True


# ==================== 自检 ====================
if __name__ == "__main__":
    providers = [
        ("ZhihuSearch", ZhihuSearchProvider()),
        ("XHSSearch", XHSSearchProvider()),
    ]

    for name, provider in providers:
        print(f"\n{'='*40}")
        print(f"  测试: {name}")
        print(f"{'='*40}")
        ok = provider.health_check()
        print(f"  {'✅' if ok else '⚠️'} health_check() — Phase 1 实现")

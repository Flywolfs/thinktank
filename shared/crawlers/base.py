"""所有采集 Provider 的基类和统一数据结构"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class SearchParams:
    """统一的搜索参数，所有 Provider 共用"""
    query: str                             # 搜索关键词
    time_start: datetime | None = None     # 起始时间，None=不限制
    time_end: datetime | None = None       # 结束时间，None=当天
    max_results: int = 20                  # 最大返回条数
    extra: dict = field(default_factory=dict)  # 平台特有参数


@dataclass
class SearchResult:
    """所有采集器返回的统一数据结构"""
    source: str           # 数据源标识，如 'bilibili', 'zhihu', 'weibo_hot'
    source_type: str      # 'social', 'news', 'knowledge', 'enterprise', 'hot'
    url: str
    title: str
    content: str          # 正文/摘要/ASR转写文本
    author: str = ''
    published_at: datetime | None = None
    metadata: dict = field(default_factory=dict)  # 平台特有字段
    raw_html: str = ''    # 原始 HTML（可选，用于后续重新解析）


class BaseProvider(ABC):
    """所有采集 Provider 的基类"""

    @abstractmethod
    def search(self, params: SearchParams) -> list[SearchResult]:
        """搜索接口 — 每个 Provider 自行决定如何处理时间参数"""
        ...

    @abstractmethod
    def health_check(self) -> bool:
        """验证数据源是否可用"""
        ...

    def get_source_name(self) -> str:
        """返回数据源唯一标识"""
        raise NotImplementedError

    def supports_time_filter(self) -> bool:
        """
        声明此 Provider 是否原生支持时间过滤。
        搜索引擎类返回 True，社交媒体/知识库类返回 False。
        """
        return False

    def _filter_by_time(
        self, results: list[SearchResult], params: SearchParams
    ) -> list[SearchResult]:
        """通用本地时间过滤，子类可选调用。
        只过滤有 published_at 的结果，没有时间数据的保留不丢。"""
        if not params.time_start and not params.time_end:
            return results
        start = params.time_start or datetime.min
        end = params.time_end or datetime.now()
        return [
            r for r in results
            if not r.published_at  # 无时间数据 → 保留
            or (start <= r.published_at <= end)
        ]

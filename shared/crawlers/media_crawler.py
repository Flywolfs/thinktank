"""MediaCrawler 集成 — 知乎/小红书/微博 Provider

通过子进程调用 MediaCrawler (https://github.com/NanmiCoder/MediaCrawler):
    uv run main.py --platform <xhs|wb|zhihu> --type search --keywords <kw> \
        --lt cookie --cookies "<cookie>" --save_data_option jsonl \
        --crawler_max_notes_count N --get_comment no

输出: data/{platform}/search/*.jsonl (JSON Lines)

前置条件:
1. MediaCrawler 已克隆并 uv sync (默认 /home/zhangchi/Documents/MediaCrawler)
2. 各平台 Cookie 已配置 (通过 .env: XHS_COOKIE / WEIBO_COOKIE / ZHIHU_COOKIE)
   Cookie 获取: bb-browser 登录平台后 F12 → Application → Cookies
"""

import json
import os
import subprocess
from pathlib import Path
from datetime import datetime

from shared.crawlers.base import BaseProvider, SearchParams, SearchResult
from shared.utils import config

MEDIACRAWLER_DIR = Path(os.getenv("MEDIACRAWLER_DIR", "/home/zhangchi/Documents/MediaCrawler"))


class _MediaCrawlerBase(BaseProvider):
    """MediaCrawler 子进程调用的公共逻辑"""

    SOURCE_TYPE = "social"
    PLATFORM = ""       # xhs / wb / zhihu
    COOKIE_ENV = ""     # .env 中的 cookie 变量名
    DEFAULT_MAX = 15

    def _run_search(self, query: str, max_results: int = 15, timeout: int = 180) -> list[dict]:
        """调用 MediaCrawler 搜索，返回原始记录列表"""
        cookie = getattr(config, self.COOKIE_ENV, "") or os.getenv(self.COOKIE_ENV, "")
        if not cookie:
            raise ConnectionError(
                f"{self.COOKIE_ENV} 未配置。请登录 {self.PLATFORM} 后 "
                f"在 .env 中填入 Cookie"
            )

        # 清空旧输出
        out_dir = MEDIACRAWLER_DIR / "data" / self.PLATFORM / "search"
        if out_dir.exists():
            for f in out_dir.glob("*.jsonl"):
                f.unlink()

        cmd = [
            "uv", "run", "main.py",
            "--platform", self.PLATFORM,
            "--type", "search",
            "--keywords", query,
            "--lt", "cookie",
            "--cookies", cookie,
            "--save_data_option", "jsonl",
            "--crawler_max_notes_count", str(min(max_results, 30)),
            "--get_comment", "no",
            "--headless", "yes",
        ]
        try:
            result = subprocess.run(
                cmd,
                cwd=str(MEDIACRAWLER_DIR),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            # 超时但可能已写了部分数据
            pass
        except FileNotFoundError:
            raise ConnectionError(f"MediaCrawler 不存在: {MEDIACRAWLER_DIR}，请先克隆并 uv sync")

        # 读取输出
        records = []
        if out_dir.exists():
            for f in out_dir.glob("*.jsonl"):
                for line in f.read_text(encoding="utf-8").strip().splitlines():
                    if line.strip():
                        try:
                            records.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
        return records

    def _parse_records(self, records: list[dict], query: str) -> list[SearchResult]:
        """子类实现：把 MediaCrawler 记录转为 SearchResult"""
        raise NotImplementedError

    def search(self, params: SearchParams) -> list[SearchResult]:
        records = self._run_search(params.query, params.max_results)
        results = self._parse_records(records, params.query)
        return self._filter_by_time(results, params)

    def health_check(self) -> bool:
        """检查 MediaCrawler 目录存在 + cookie 已配置"""
        if not MEDIACRAWLER_DIR.exists():
            return False
        cookie = getattr(config, self.COOKIE_ENV, "") or os.getenv(self.COOKIE_ENV, "")
        return bool(cookie)


class ZhihuSearchProvider(_MediaCrawlerBase):
    """知乎搜索 — 按实体名搜问答/文章"""

    SOURCE = "zhihu"
    PLATFORM = "zhihu"
    COOKIE_ENV = "ZHIHU_COOKIE"

    def _parse_records(self, records: list[dict], query: str) -> list[SearchResult]:
        results = []
        for r in records:
            # MediaCrawler 知乎记录字段: 根据实际输出调整
            title = r.get("title") or r.get("question") or r.get("name") or ""
            url = r.get("url") or r.get("link") or ""
            content = r.get("content") or r.get("excerpt") or r.get("description") or ""
            author = r.get("author") or r.get("author_name") or ""
            pub = r.get("publish_time") or r.get("created_at") or r.get("created_time") or ""
            if not title:
                continue
            results.append(SearchResult(
                source=self.SOURCE,
                source_type=self.SOURCE_TYPE,
                url=url,
                title=title,
                content=content[:1000],
                author=author,
                published_at=self._parse_time(pub),
                metadata={"platform": "zhihu", "raw": r},
            ))
        return results

    @staticmethod
    def _parse_time(v):
        if isinstance(v, (int, float)) and v > 10**10:  # 毫秒
            return datetime.fromtimestamp(v / 1000)
        if isinstance(v, (int, float)):
            return datetime.fromtimestamp(v)
        return None


class XHSSearchProvider(_MediaCrawlerBase):
    """小红书搜索 — 按实体名搜笔记"""

    SOURCE = "xiaohongshu"
    PLATFORM = "xhs"
    COOKIE_ENV = "XHS_COOKIE"

    def _parse_records(self, records: list[dict], query: str) -> list[SearchResult]:
        results = []
        for r in records:
            title = r.get("title") or r.get("note_title") or ""
            url = r.get("url") or f"https://www.xiaohongshu.com/explore/{r.get('note_id', '')}"
            content = r.get("content") or r.get("desc") or r.get("description") or ""
            author = r.get("author") or r.get("nickname") or r.get("author_name") or ""
            pub = r.get("publish_time") or r.get("create_time") or r.get("created_time") or ""
            likes = r.get("liked_count") or r.get("like_count") or r.get("likes") or 0
            if not title:
                continue
            results.append(SearchResult(
                source=self.SOURCE,
                source_type=self.SOURCE_TYPE,
                url=url,
                title=title,
                content=content[:1000],
                author=author,
                published_at=self._parse_time(pub),
                metadata={"platform": "xhs", "likes": likes, "raw": r},
            ))
        return results

    @staticmethod
    def _parse_time(v):
        if isinstance(v, (int, float)) and v > 10**10:
            return datetime.fromtimestamp(v / 1000)
        if isinstance(v, (int, float)):
            return datetime.fromtimestamp(v)
        return None


class WeiboSearchProvider(_MediaCrawlerBase):
    """微博搜索 — 按实体名搜帖子"""

    SOURCE = "weibo"
    PLATFORM = "wb"
    COOKIE_ENV = "WEIBO_COOKIE"

    def _parse_records(self, records: list[dict], query: str) -> list[SearchResult]:
        results = []
        for r in records:
            title = r.get("title") or r.get("content") or ""
            if not title:
                continue
            url = r.get("url") or r.get("detail_url") or ""
            content = r.get("content") or r.get("text") or ""
            author = r.get("author") or r.get("author_name") or r.get("nickname") or ""
            pub = r.get("publish_time") or r.get("created_at") or r.get("time") or ""
            reposts = r.get("reposts_count") or r.get("repost_count") or 0
            comments = r.get("comments_count") or r.get("comment_count") or 0
            results.append(SearchResult(
                source=self.SOURCE,
                source_type=self.SOURCE_TYPE,
                url=url,
                title=title[:200],
                content=content[:1000],
                author=author,
                published_at=self._parse_time(pub),
                metadata={"platform": "weibo", "reposts": reposts, "comments": comments, "raw": r},
            ))
        return results

    @staticmethod
    def _parse_time(v):
        if isinstance(v, (int, float)) and v > 10**10:
            return datetime.fromtimestamp(v / 1000)
        if isinstance(v, (int, float)):
            return datetime.fromtimestamp(v)
        return None


# ==================== 自检 ====================
if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    providers = [
        ("知乎", ZhihuSearchProvider()),
        ("小红书", XHSSearchProvider()),
        ("微博", WeiboSearchProvider()),
    ]

    for name, p in providers:
        print(f"\n{'='*40}")
        print(f"  {name}: {p.SOURCE}")
        print(f"{'='*40}")
        ok = p.health_check()
        print(f"  health_check: {'✅' if ok else '❌ (cookie 未配置)'}")
        if ok:
            try:
                results = p.search(SearchParams(query="雷军", max_results=5))
                print(f"  搜索 '雷军' → {len(results)} 条")
                for r in results[:5]:
                    print(f"    - {r.title[:50]}")
            except Exception as e:
                print(f"  ❌ {e}")

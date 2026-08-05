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
import re
import subprocess
from pathlib import Path
from datetime import datetime

from shared.crawlers.base import BaseProvider, SearchParams, SearchResult
from shared.utils import config

MEDIACRAWLER_DIR = Path(os.getenv("MEDIACRAWLER_DIR", "/home/zhangchi/Documents/MediaCrawler"))
# 每次请求的原始数据存档目录（think_tank/data/raw/{platform}/）
RAW_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "raw"


class _MediaCrawlerBase(BaseProvider):
    """MediaCrawler 子进程调用的公共逻辑"""

    SOURCE_TYPE = "social"
    PLATFORM = ""       # CLI 平台名: xhs / wb / zhihu
    OUTPUT_NAME = ""    # 输出目录名: xhs / weibo / zhihu（与 CLI 名可能不同）
    COOKIE_ENV = ""     # .env 中的 cookie 变量名
    DEFAULT_MAX = 15

    def _run_search(self, query: str, max_results: int = 15, timeout: int = 180) -> list[dict]:
        """调用 MediaCrawler 搜索，返回原始记录列表。
        注意: MediaCrawler 默认 CDP 模式连接已登录的 Chrome（ENABLE_CDP_MODE=True），
        直接用浏览器登录态，cookie 是可选 fallback。"""
        cookie = getattr(config, self.COOKIE_ENV, "") or os.getenv(self.COOKIE_ENV, "")
        output_name = self.OUTPUT_NAME or self.PLATFORM

        # 清空旧输出（MediaCrawler 输出目录: data/{全称平台名}/jsonl/）
        out_dir = MEDIACRAWLER_DIR / "data" / output_name / "jsonl"
        if out_dir.exists():
            for f in out_dir.glob("*.jsonl"):
                f.unlink()

        cmd = [
            "uv", "run", "main.py",
            "--platform", self.PLATFORM,
            "--type", "search",
            "--keywords", query,
            "--lt", "cookie" if cookie else "qrcode",
            "--save_data_option", "jsonl",
            "--crawler_max_notes_count", str(min(max_results, 30)),
            "--get_comment", "no",
            "--headless", "yes",
        ]
        if cookie:
            cmd += ["--cookies", cookie]
        try:
            # 用 bytes 模式 + errors='replace' 容错解码：
            # MediaCrawler rich 日志可能含非 UTF-8 字节，text=True 会抛 UnicodeDecodeError
            result = subprocess.run(
                cmd,
                cwd=str(MEDIACRAWLER_DIR),
                capture_output=True,
                timeout=timeout,
            )
            # 容错解码（日志只用于调试，坏字节替换即可）
            _ = result.stdout.decode("utf-8", errors="replace") if result.stdout else ""
            _ = result.stderr.decode("utf-8", errors="replace") if result.stderr else ""
        except subprocess.TimeoutExpired:
            # 超时但可能已写了部分数据
            pass
        except FileNotFoundError:
            raise ConnectionError(f"MediaCrawler 不存在: {MEDIACRAWLER_DIR}，请先克隆并 uv sync")

        # 读取输出，并把本次原始数据存档到 think_tank/data/raw/
        records = []
        if out_dir.exists():
            raw_lines = []  # 收集本次所有 jsonl 原始行
            for f in out_dir.glob("*.jsonl"):
                try:
                    content = f.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    continue
                for line in content.strip().splitlines():
                    if line.strip():
                        raw_lines.append(line.strip())
                        try:
                            records.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
            # 存档：每次请求一个文件，带时间戳+关键词，不覆盖
            if raw_lines:
                self._archive_raw(output_name, query, raw_lines)
        return records

    def _archive_raw(self, output_name: str, query: str, raw_lines: list[str]):
        """把本次请求的原始 jsonl 行存档到 data/raw/{platform}/{ts}_{keyword}.jsonl"""
        try:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            # 关键词做文件名字符清理
            safe_q = re.sub(r'[\\/:*?"<>|]', "_", query)[:40]
            out_file = RAW_DATA_DIR / output_name / f"{ts}_{safe_q}.jsonl"
            out_file.parent.mkdir(parents=True, exist_ok=True)
            out_file.write_text("\n".join(raw_lines) + "\n", encoding="utf-8")
        except Exception as e:
            # 存档失败不影响主流程
            print(f"[media_crawler] 原始数据存档失败: {e}")

    def _parse_records(self, records: list[dict], query: str) -> list[SearchResult]:
        """子类实现：把 MediaCrawler 记录转为 SearchResult"""
        raise NotImplementedError

    def search(self, params: SearchParams) -> list[SearchResult]:
        records = self._run_search(params.query, params.max_results)
        results = self._parse_records(records, params.query)
        return self._filter_by_time(results, params)

    def health_check(self) -> bool:
        """检查 MediaCrawler 目录存在。
        注意: MediaCrawler 默认 CDP 模式连接已登录的 Chrome（复用浏览器登录态），
        cookie 参数是可选的 fallback，不作为启用的必要条件。"""
        return MEDIACRAWLER_DIR.exists()


class ZhihuSearchProvider(_MediaCrawlerBase):
    """知乎搜索 — 按实体名搜问答/文章"""

    SOURCE = "zhihu"
    PLATFORM = "zhihu"
    COOKIE_ENV = "ZHIHU_COOKIE"

    def _parse_records(self, records: list[dict], query: str) -> list[SearchResult]:
        results = []
        for r in records:
            # MediaCrawler 知乎记录实际字段（见 data/zhihu/jsonl/*.jsonl）
            title = r.get("title") or r.get("question_name") or ""
            url = r.get("content_url") or ""
            content = r.get("content_text") or r.get("desc") or ""
            author = r.get("user_nickname") or ""
            pub = r.get("created_time") or ""
            if not title and not content:
                continue
            results.append(SearchResult(
                source=self.SOURCE,
                source_type=self.SOURCE_TYPE,
                url=url,
                title=title or content[:50],
                content=content[:1000],
                author=author,
                published_at=self._parse_time(pub),
                metadata={
                    "platform": "zhihu",
                    "content_id": r.get("content_id"),
                    "content_type": r.get("content_type"),
                    "voteup": r.get("voteup_count"),
                    "comments": r.get("comment_count"),
                },
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
    OUTPUT_NAME = "weibo"  # CLI 平台名是 wb，但输出目录是 data/weibo/
    COOKIE_ENV = "WEIBO_COOKIE"

    def _parse_records(self, records: list[dict], query: str) -> list[SearchResult]:
        results = []
        for r in records:
            # MediaCrawler 微博记录实际字段（见 data/weibo/jsonl/*.jsonl）
            title = r.get("title") or ""
            content = r.get("content") or r.get("text") or ""
            url = r.get("url") or r.get("detail_url") or ""
            if not title and not content:
                continue
            author = r.get("nickname") or r.get("author") or ""
            pub = r.get("create_time") or r.get("created_at") or ""
            results.append(SearchResult(
                source=self.SOURCE,
                source_type=self.SOURCE_TYPE,
                url=url,
                title=title or content[:50],
                content=content[:1000],
                author=author,
                published_at=self._parse_time(pub),
                metadata={
                    "platform": "weibo",
                    "note_id": r.get("note_id"),
                    "likes": r.get("liked_count"),
                    "comments": r.get("comments_count"),
                },
            ))
        return results

    @staticmethod
    def _parse_time(v):
        if isinstance(v, (int, float)) and v > 10**10:  # 毫秒
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

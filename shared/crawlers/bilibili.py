"""B站数据源 — 基于 bilibili-cli

bilibili-cli (public-clis/bilibili-cli, 943⭐) 提供:
- bili search:  关键词搜索视频/用户，内部处理 Wbi 签名
- bili video:   视频详情、字幕、评论、AI 总结
- bili audio:   下载音频 + 切分为 ASR-ready 16kHz mono WAV 片段
- bili hot:     热门视频
- bili rank:    排行榜

相比 RSSHub + bilix 方案的优势:
1. 一条命令搜视频 (内部处理 Wbi 签名，不需要 RSSHub)
2. audio 子命令直接输出 ASR-ready WAV（不需要 ffmpeg 中间步骤）
3. video 子命令自带评论抓取
4. JSON/YAML 输出，解析简单
5. 自动从浏览器提取 Cookie，无需手动配置

安装: uv tool install "bilibili-cli[audio]"
"""

import json
import subprocess
from datetime import datetime

from shared.crawlers.base import BaseProvider, SearchParams, SearchResult


class BilibiliCLIProvider(BaseProvider):
    """B站统一 Provider — 基于 bilibili-cli，覆盖搜索 + 详情 + 音频 + 评论"""

    SOURCE = "bilibili"
    SOURCE_TYPE = "social"
    BINARY = "bili"

    # ── 视频搜索 ─────────────────────────────────────────

    def search(self, params: SearchParams) -> list[SearchResult]:
        return self.search_videos(params)

    def search_videos(self, params: SearchParams) -> list[SearchResult]:
        """搜索 B站视频"""
        output = self._run(
            "search", params.query,
            "--type", "video",
            "--max", str(min(params.max_results, 50)),
            "--json",
        )
        data = json.loads(output)
        if not data.get("ok"):
            raise RuntimeError(f"B站搜索失败: {data}")

        results = []
        for item in data.get("data", []):
            result = SearchResult(
                source=self.SOURCE,
                source_type=self.SOURCE_TYPE,
                url=f"https://www.bilibili.com/video/{item['bvid']}",
                title=item.get("title", ""),
                content="",
                author=item.get("author", ""),
                metadata={
                    "bvid": item.get("bvid"),
                    "aid": item.get("aid"),
                    "duration": item.get("duration"),
                    "play": item.get("play", 0),
                },
            )
            results.append(result)

        return self._filter_by_time(results, params)

    # ── 视频详情 ─────────────────────────────────────────

    def get_video_detail(self, bvid: str) -> SearchResult | None:
        """获取单个视频详细信息（含简介、字幕、AI总结）"""
        output = self._run("video", bvid, "--json")
        data = json.loads(output)
        if not data.get("ok"):
            return None

        video = data["data"]["video"]
        subtitle = data["data"].get("subtitle", {})
        ai_summary = data["data"].get("ai_summary", "")
        if isinstance(ai_summary, dict):
            ai_summary = ai_summary.get("text", "")

        content_parts = [video.get("description", "")]
        if subtitle.get("text"):
            content_parts.append(f"\n[字幕]\n{subtitle['text']}")
        if ai_summary:
            content_parts.append(f"\n[AI总结]\n{ai_summary}")

        return SearchResult(
            source=self.SOURCE,
            source_type=self.SOURCE_TYPE,
            url=video.get("url", ""),
            title=video.get("title", ""),
            content="\n".join(content_parts),
            author=video.get("owner", {}).get("name", ""),
            metadata={
                "bvid": video.get("bvid"),
                "aid": video.get("aid"),
                "duration_seconds": video.get("duration_seconds"),
                "stats": video.get("stats", {}),
                "has_subtitle": subtitle.get("available", False),
                "has_ai_summary": bool(ai_summary),
            },
        )

    # ── 评论 ─────────────────────────────────────────────

    def get_comments(self, bvid: str, max_results: int = 20) -> list[SearchResult]:
        """获取视频评论"""
        output = self._run("video", bvid, "--comments", "--json")
        data = json.loads(output)
        if not data.get("ok"):
            return []

        comments = data["data"].get("comments", [])
        results = []
        for c in comments[:max_results]:
            results.append(SearchResult(
                source=self.SOURCE + "_comment",
                source_type=self.SOURCE_TYPE,
                url=f"https://www.bilibili.com/video/{bvid}",
                title=f"评论 by {c.get('username', '')}",
                content=c.get("content", ""),
                author=c.get("username", ""),
                published_at=datetime.fromtimestamp(c["created_at"])
                if c.get("created_at") else None,
                metadata={
                    "bvid": bvid,
                    "rpid": c.get("rpid"),
                    "like": c.get("like", 0),
                    "reply_count": c.get("reply_count", 0),
                },
            ))
        return results

    # ── 音频下载 (ASR 专用) ──────────────────────────────

    def download_audio_asr(
        self,
        bvid: str,
        output_dir: str | None = None,
        segment_seconds: int = 25,
    ) -> list[str]:
        """
        下载视频音频并切分为 ASR-ready WAV 片段。
        返回 WAV 文件绝对路径列表（16kHz mono PCM）。

        默认每段 25s，用 merge_segments() 合并到目标时长后再送 ASR。
        """
        import os as _os
        from pathlib import Path as _Path

        if not output_dir:
            output_dir = f"/tmp/bili_asr_{bvid}"
        _Path(output_dir).mkdir(parents=True, exist_ok=True)

        result = subprocess.run(
            [self.BINARY, "audio", bvid, "--segment", str(segment_seconds),
             "-o", output_dir],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            raise RuntimeError(f"bili audio 失败: {result.stderr}")

        wav_files = sorted([
            _os.path.join(output_dir, f)
            for f in _os.listdir(output_dir)
            if f.endswith(".wav")
        ])
        return wav_files

    @staticmethod
    def merge_segments(
        wav_files: list[str],
        target_seconds: int = 240,
        output_dir: str | None = None,
    ) -> list[str]:
        """
        将 25s 小片段合并成更长的 WAV 文件，减少 ASR API 调用次数。

        Args:
            wav_files:  排序后的 WAV 文件路径列表（如 bili audio 的 25s 片段）
            target_seconds: 目标每段时长（默认 240s = 4分钟，匹配 Qwen3-ASR 最佳输入）
            output_dir: 合并后文件输出目录（默认与第一段同目录下的 merged/）

        Returns:
            合并后的 WAV 文件绝对路径列表

        Example:
            segs = provider.download_audio_asr("BVxxx")      # 28 个 25s 片段
            merged = provider.merge_segments(segs, 240)       # → 3 个 ~240s 文件
        """
        import os as _os
        from pathlib import Path as _Path
        import math

        # 估算每段实际秒数（从第一段文件大小推算，16kHz mono 16bit = 32KB/s）
        if wav_files:
            first_size = _Path(wav_files[0]).stat().st_size
            segment_seconds = max(first_size / 32000, 1)  # 至少 1s
        else:
            return []

        segments_per_chunk = max(1, math.ceil(target_seconds / segment_seconds))
        total = len(wav_files)
        num_chunks = math.ceil(total / segments_per_chunk)

        if not output_dir:
            output_dir = str(_Path(wav_files[0]).parent / "merged")
        _Path(output_dir).mkdir(parents=True, exist_ok=True)

        merged_files = []
        for i in range(num_chunks):
            start = i * segments_per_chunk
            end = min(start + segments_per_chunk, total)
            chunk = wav_files[start:end]

            if len(chunk) == 1:
                # 单段无需合并，直接软链接
                merged_path = _os.path.join(output_dir, f"merged_{i:03d}.wav")
                _os.symlink(_os.path.abspath(chunk[0]), merged_path)
                merged_files.append(merged_path)
            else:
                # 用 ffmpeg concat 合并多段
                concat_list = _os.path.join(output_dir, f"concat_{i:03d}.txt")
                with open(concat_list, "w") as f:
                    for p in chunk:
                        f.write(f"file '{_os.path.abspath(p)}'\n")

                merged_path = _os.path.join(output_dir, f"merged_{i:03d}.wav")
                subprocess.run(
                    ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                     "-i", concat_list, "-c", "copy", merged_path],
                    capture_output=True,
                    timeout=30,
                )
                merged_files.append(merged_path)
                _os.remove(concat_list)  # 清理临时文件

        return merged_files

    def download_audio_full(self, bvid: str, output_dir: str | None = None) -> str:
        """下载完整音频文件（不切分），返回 m4a 路径"""
        args = ["audio", bvid, "--no-split"]
        if output_dir:
            args.extend(["-o", output_dir])

        result = subprocess.run(
            [self.BINARY, *args],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            raise RuntimeError(f"bili audio 失败: {result.stderr}")

        for line in result.stdout.split("\n"):
            if line.strip().endswith(".m4a"):
                return line.strip()
        raise FileNotFoundError(f"未在输出中找到 m4a 文件: {result.stdout}")

    # ── 热门 ─────────────────────────────────────────────

    def get_hot_videos(self, max_results: int = 20) -> list[SearchResult]:
        """获取热门视频"""
        output = self._run("hot", "--max", str(max_results), "--json")
        data = json.loads(output)
        if not data.get("ok"):
            return []
        items = data.get("data", [])
        # data 可能是 list 或 dict
        if isinstance(items, dict):
            items = items.get("items", items.get("videos", []))
        return self._parse_video_list(items)

    # ── 排行榜 ───────────────────────────────────────────

    def get_ranking(self, max_results: int = 20) -> list[SearchResult]:
        """获取全站排行榜"""
        output = self._run("rank", "--max", str(max_results), "--json")
        data = json.loads(output)
        if not data.get("ok"):
            return []
        items = data.get("data", [])
        if isinstance(items, dict):
            items = items.get("items", items.get("videos", []))
        return self._parse_video_list(items)

    # ── 健康检查 ─────────────────────────────────────────

    def health_check(self) -> bool:
        try:
            output = self._run("search", "test", "--type", "video",
                               "--max", "1", "--json")
            data = json.loads(output)
            return data.get("ok", False)
        except Exception:
            return False

    # ── 内部工具 ─────────────────────────────────────────

    def _run(self, *args: str) -> str:
        """执行 bili 命令并返回 stdout"""
        result = subprocess.run(
            [self.BINARY, *args],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"bili {' '.join(args[:2])} 失败:\n{result.stderr}"
            )
        return result.stdout

    def _parse_video_list(self, items: list[dict]) -> list[SearchResult]:
        """通用视频列表解析"""
        results = []
        for item in items:
            results.append(SearchResult(
                source=self.SOURCE,
                source_type=self.SOURCE_TYPE,
                url=f"https://www.bilibili.com/video/{item['bvid']}",
                title=item.get("title", ""),
                content=item.get("description", ""),
                author=item.get("author", ""),
                metadata={
                    "bvid": item.get("bvid"),
                    "play": item.get("play", 0),
                    "duration": item.get("duration", ""),
                },
            ))
        return results


# 向后兼容别名
BilibiliSearchProvider = BilibiliCLIProvider
BilibiliCommentProvider = BilibiliCLIProvider  # 通过 get_comments() 方法
BilibiliAudioProvider = BilibiliCLIProvider    # 通过 download_audio_*() 方法


# ==================== 自检 ====================
if __name__ == "__main__":
    provider = BilibiliCLIProvider()

    print("=" * 50)
    print("  BilibiliCLIProvider 自检")
    print("=" * 50)

    # 1. 健康检查
    ok = provider.health_check()
    print(f"  {'✅' if ok else '❌'} health_check()")

    if not ok:
        exit(1)

    # 2. 视频搜索
    print("\n  📹 视频搜索: '雷军'")
    results = provider.search_videos(SearchParams(query="雷军", max_results=3))
    for r in results:
        print(f"     [{r.metadata.get('play', 0)}播放] {r.title[:60]}")
        print(f"     BV:{r.metadata['bvid']} | {r.author}")

    # 3. 视频详情
    if results:
        bvid = results[0].metadata["bvid"]
        print(f"\n  📋 视频详情: {bvid}")
        detail = provider.get_video_detail(bvid)
        if detail:
            desc = detail.content[:120].replace("\n", " ")
            print(f"     标题: {detail.title}")
            print(f"     简介: {desc}...")
            if detail.metadata.get("has_subtitle"):
                print(f"     ✅ 有字幕")
            if detail.metadata.get("has_ai_summary"):
                print(f"     ✅ 有 AI 总结")

    # 4. 评论
    if results:
        bvid = results[0].metadata["bvid"]
        print(f"\n  💬 评论: {bvid}")
        comments = provider.get_comments(bvid, max_results=3)
        # 可能需要登录才能获取评论
        if comments:
            for c in comments:
                print(f"     {c.author}: {c.content[:60]}...")
        else:
            print(f"     ⚠️ 无评论（可能需要 bili login 登录）")

    # 5. 音频下载 + 合并测试
    if results:
        bvid = results[0].metadata["bvid"]
        print(f"\n  🎵 音频下载: {bvid}")
        try:
            segs = provider.download_audio_asr(bvid)
            print(f"     原始片段: {len(segs)} 个 25s WAV")

            merged = provider.merge_segments(segs, target_seconds=240)
            print(f"     合并后: {len(merged)} 个 ~240s WAV")
            for m in merged:
                import os
                size_mb = os.path.getsize(m) / 1024 / 1024
                dur_s = size_mb / 0.032  # 16kHz mono 16bit ≈ 32KB/s
                print(f"       {os.path.basename(m)} ({size_mb:.1f}MB ≈ {dur_s:.0f}s)")
        except Exception as e:
            print(f"     ⚠️ {e}")

    # 6. 热门 / 排行榜
    print(f"\n  🔥 热门视频:")
    try:
        hot = provider.get_hot_videos(max_results=3)
        for h in hot:
            print(f"     {h.title[:50]}")
    except Exception as e:
        print(f"     ⚠️ {e}")

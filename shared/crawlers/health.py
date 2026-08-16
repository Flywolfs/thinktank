"""Provider 健康检测器（UI 数据源状态面板）

对每个数据源做**有意义的**健康检查，返回结构化状态:
- ok / error / warning + 消息 + 耗时
- 层级: 快速检查（配置/端口/进程）→ 深度检查（实际搜索 test）

关键改进: MediaCrawler 三平台原来只查目录存在，导致登录失效/Chrome 退出
仍显示"正常"。现在额外检查:
  - Chrome CDP 9222 端口（bb-browser 在不在）
  - Cookie 是否配置（.env 有值）
  - 可选: 实际搜索验证（登录态真的有效）
"""

from __future__ import annotations

import socket
import time
from dataclasses import dataclass, field
from pathlib import Path

from shared.utils import config


@dataclass
class ProviderStatus:
    """单个数据源的健康状态"""
    name: str
    status: str = "unknown"          # ok / error / warning / disabled
    message: str = ""
    duration_ms: int = 0
    checks: list[dict] = field(default_factory=list)  # 子检查明细

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "status": self.status,
            "message": self.message,
            "duration_ms": self.duration_ms,
            "checks": self.checks,
        }


class ProviderHealthChecker:
    """遍历所有数据源做健康检测"""

    # ── 公共检查工具 ───────────────────────────────────

    @staticmethod
    def check_port(port: int, host: str = "127.0.0.1") -> bool:
        """检查本地端口是否监听"""
        try:
            with socket.create_connection((host, port), timeout=2):
                return True
        except OSError:
            return False

    @staticmethod
    def check_http(url: str, timeout: float = 5) -> bool:
        """检查 HTTP 端点可达"""
        try:
            import httpx
            r = httpx.get(url, timeout=timeout)
            return r.status_code < 500
        except Exception:
            return False

    # ── 各源检测 ───────────────────────────────────────

    def check_wikipedia(self) -> ProviderStatus:
        t0 = time.time()
        st = ProviderStatus(name="wikipedia")
        try:
            ok = self.check_http("https://zh.wikipedia.org/w/api.php?action=query&list=search&srsearch=test&format=json")
            st.status = "ok" if ok else "error"
            st.message = "维基百科 API 可达" if ok else "维基百科 API 不可达（网络/区域限制）"
            st.checks.append({"check": "api", "ok": ok})
        except Exception as e:
            st.status = "error"
            st.message = f"检测异常: {e}"
        st.duration_ms = int((time.time() - t0) * 1000)
        return st

    def check_serper(self) -> ProviderStatus:
        t0 = time.time()
        st = ProviderStatus(name="serper")
        if not config.SERPER_API_KEY:
            st.status = "error"
            st.message = "SERPER_API_KEY 未配置（.env）"
            return st
        try:
            import httpx
            r = httpx.post(
                "https://google.serper.dev/search",
                json={"q": "test"},
                headers={"X-API-KEY": config.SERPER_API_KEY, "Content-Type": "application/json"},
                timeout=8,
            )
            ok = r.status_code == 200
            st.status = "ok" if ok else "error"
            st.message = "Google 搜索 API 正常" if ok else f"Serper 返回 {r.status_code}"
            st.checks.append({"check": "api", "ok": ok, "status_code": r.status_code if not ok else None})
        except Exception as e:
            st.status = "error"
            st.message = f"Serper 调用失败: {e}"
        st.duration_ms = int((time.time() - t0) * 1000)
        return st

    def check_bilibili(self) -> ProviderStatus:
        t0 = time.time()
        st = ProviderStatus(name="bilibili")
        # bilibili-cli 是否安装
        import shutil
        import subprocess
        import json
        if not shutil.which("bili"):
            st.status = "error"
            st.message = "bilibili-cli 未安装（uv tool install bilibili-cli）"
            return st
        try:
            r = subprocess.run(
                ["bili", "search", "test", "--type", "video", "--max", "1", "--json"],
                capture_output=True, text=True, timeout=20,
            )
            ok = r.returncode == 0
            st.status = "ok" if ok else "error"
            st.message = "B站 API 正常" if ok else f"bili 返回码 {r.returncode}"
            st.checks.append({"check": "cli", "ok": ok})
        except subprocess.TimeoutExpired:
            st.status = "error"
            st.message = "bili search 超时"
        except Exception as e:
            st.status = "error"
            st.message = f"bili 调用失败: {e}"
        st.duration_ms = int((time.time() - t0) * 1000)
        return st

    def check_enterprise(self) -> ProviderStatus:
        """企业信息依赖 serper，检查 serper key + 可达即可"""
        t0 = time.time()
        st = ProviderStatus(name="enterprise")
        if not config.SERPER_API_KEY:
            st.status = "error"
            st.message = "依赖 SERPER_API_KEY（未配置）"
            return st
        st.status = "ok"
        st.message = "依赖 Serper（见 serper 状态）"
        st.checks.append({"check": "dep", "ok": True, "depends_on": "serper"})
        st.duration_ms = int((time.time() - t0) * 1000)
        return st

    def check_mediacrawler(self, platform: str, source_name: str,
                           cookie_env: str, output_dir: str) -> ProviderStatus:
        """MediaCrawler 三平台（zhihu/xhs/weibo）检测:
        1. MediaCrawler 目录存在
        2. Chrome CDP 9222 端口（bb-browser 在不在）
        3. Cookie 是否配置
        """
        t0 = time.time()
        st = ProviderStatus(name=source_name)
        media_dir = Path(config.get("MEDIACRAWLER_DIR", "/home/zhangchi/Documents/MediaCrawler"))

        # 1. 目录
        dir_ok = media_dir.exists()
        st.checks.append({"check": "dir", "ok": dir_ok, "path": str(media_dir)})

        # 2. CDP 端口
        cdp_ok = self.check_port(9222)
        st.checks.append({"check": "cdp_9222", "ok": cdp_ok})

        # 3. Cookie
        cookie = getattr(config, cookie_env, "") or ""
        cookie_ok = bool(cookie and len(cookie) > 20)
        st.checks.append({"check": "cookie", "ok": cookie_ok, "env": cookie_env})

        # 汇总
        problems = []
        if not dir_ok:
            problems.append("MediaCrawler 目录不存在")
        if not cdp_ok:
            problems.append("Chrome CDP(9222) 未启动 — 需 bb-browser open")
        if not cookie_ok:
            problems.append(f"{cookie_env} 未配置")

        if not problems:
            st.status = "ok"
            st.message = "就绪（CDP + Cookie 正常）"
        elif cdp_ok and cookie_ok and dir_ok:
            # 都满足但可能登录失效 → warning（实际搜索才知道）
            st.status = "ok"
            st.message = "基础检查通过（登录有效性建议搜索验证）"
        else:
            st.status = "error"
            st.message = "; ".join(problems)
        st.duration_ms = int((time.time() - t0) * 1000)
        return st

    # ── 深度检测（实际搜索） ───────────────────────────

    def deep_search_check(self, source_name: str, query: str = "雷军",
                          max_results: int = 3) -> ProviderStatus:
        """实际调用搜索验证登录态/数据源真的能用（慢，供 UI 手动触发）"""
        from shared.tools.registry import get_registry

        t0 = time.time()
        st = ProviderStatus(name=source_name)
        reg = get_registry()
        tool_map = {
            "zhihu": "search_zhihu",
            "xiaohongshu": "search_xiaohongshu",
            "weibo": "search_weibo",
            "serper": "search_serper",
            "bilibili": "search_bilibili",
            "wikipedia": "search_wikipedia",
            "enterprise": "search_enterprise",
        }
        tool = tool_map.get(source_name)
        if not tool:
            st.status = "error"
            st.message = f"未知数据源: {source_name}"
            return st
        try:
            results = reg.call(tool, query=query, max_results=max_results)
            n = len(results)
            st.status = "ok" if n > 0 else "warning"
            st.message = f"搜索成功 {n} 条" if n > 0 else "搜索返回 0 条（可能登录失效/风控/无结果）"
            st.checks.append({"check": "search", "ok": n > 0, "count": n})
        except Exception as e:
            st.status = "error"
            st.message = f"搜索失败: {e}"
        st.duration_ms = int((time.time() - t0) * 1000)
        return st

    # ── 总入口 ─────────────────────────────────────────

    def check_all(self, include_deep: bool = False) -> list[dict]:
        """检测全部数据源"""
        results = [
            self.check_wikipedia(),
            self.check_serper(),
            self.check_bilibili(),
            self.check_enterprise(),
            self.check_mediacrawler("zhihu", "zhihu", "ZHIHU_COOKIE", "zhihu"),
            self.check_mediacrawler("xhs", "xiaohongshu", "XHS_COOKIE", "xhs"),
            self.check_mediacrawler("wb", "weibo", "WEIBO_COOKIE", "weibo"),
        ]
        # 深度检测（慢，默认关闭；UI 手动触发）
        if include_deep:
            for name in ("zhihu", "xiaohongshu", "weibo", "serper", "bilibili"):
                deep = self.deep_search_check(name)
                # 合并进对应项
                for st in results:
                    if st.name == name:
                        st.checks.append({"check": "deep_search", "ok": deep.status == "ok",
                                          "message": deep.message, "duration_ms": deep.duration_ms})
                        if deep.status == "error":
                            st.status = "error"
                        elif deep.status == "warning" and st.status == "ok":
                            st.status = "warning"
                            st.message = deep.message
                        break
        return [s.to_dict() for s in results]


_default_checker: ProviderHealthChecker | None = None


def get_health_checker() -> ProviderHealthChecker:
    global _default_checker
    if _default_checker is None:
        _default_checker = ProviderHealthChecker()
    return _default_checker

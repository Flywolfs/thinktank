"""实体名称归一化管道（P4.2）

把实体名归一化为唯一 key，用于:
- visited 防环（A→B→A' 别名也防住）
- 图谱去重（"臺灣/台湾"、"MICHAEL/michael" 归为同一节点）
- 别名增量学习（LLM 判定同指 → 更新 alias 列表）

中英文分流:
  中文: 全半角统一 → 去后缀(有限公司/集团/工作室...) → 转简体(zhconv) → 别名表
  英文: lowercase → NFKC → 去冠词/后缀(Inc/Ltd/Corp...) → 复数词干化 → 别名表
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# ── 中文后缀（组织/机构名常见后缀，去掉后归并）──────────

ZH_SUFFIXES = [
    "股份有限公司", "有限责任公司", "集团有限公司", "有限公司", "集团",
    "工作室", "事务所", "研究院", "研究所", "大学", "学院",
    "银行", "基金", "基金会", "协会", "委员会", "公司", "集团",
]

# ── 英文后缀（组织名常见后缀）──────────────────────────

EN_SUFFIXES = [
    "incorporated", "inc", "limited", "ltd", "corp", "corporation",
    "company", "co", "llc", "llp", "plc", "group", "foundation",
    "university", "college", "institute", "association",
]

# ── 英文冠词 ───────────────────────────────────────────

EN_ARTICLES = ["the", "a", "an"]

# ── 已知别名映射（内置，增量学习补充）──────────────────

KNOWN_ALIASES: dict[str, list[str]] = {
    # 人物
    "罗永浩": ["老罗", "罗老师", "罗永浩(交个朋友)"],
    "蒋方舟": ["蒋方舟(作家)"],
    "韩红": ["韩红(歌手)"],
    # 公司
    "字节跳动": ["字节跳动科技", "bytedance", "字节跳动有限公司"],
    "小米": ["小米科技", "小米公司", "xiaomi", "小米集团"],
    "华为": ["华为技术", "huawei", "华为技术有限公司"],
    "阿里巴巴": ["阿里巴巴集团", "alibaba", "阿里"],
}


@dataclass
class NormalizedEntity:
    """归一化结果"""
    key: str                 # 归一化 key（入库/visited 用）
    original: str            # 原始名称
    aliases: list[str] = field(default_factory=list)  # 已知别名（含原始名）

    def to_dict(self) -> dict:
        return {"key": self.key, "original": self.original, "aliases": self.aliases}


class EntityNormalizer:
    def __init__(self):
        self._alias_reverse: dict[str, str] = {}  # 别名 → key
        # 构建反向索引（内置别名表）
        for key, aliases in KNOWN_ALIASES.items():
            norm_key = self.normalize(key)
            for a in aliases:
                self._alias_reverse[self.normalize(a)] = norm_key

    # ── 主入口 ─────────────────────────────────────────

    def normalize(self, name: str) -> str:
        """归一化实体名 → 唯一 key（先查别名反向索引，再走语言处理）"""
        if not name:
            return ""
        text = name.strip()
        if not text:
            return ""

        # 0. 别名反向索引优先（老罗 → 罗永浩）
        pre = unicodedata.normalize("NFKC", text).strip()
        pre = re.sub(r"\s+", " ", pre).strip()
        if pre in self._alias_reverse:
            return self._alias_reverse[pre]

        # 1. 全半角统一（NFKC 同时处理全角字母数字）
        text = unicodedata.normalize("NFKC", text)
        # 2. 去括号内容（"罗永浩(交个朋友)" → "罗永浩"）
        text = re.sub(r"[（(][^）)]*[）)]", "", text).strip()
        # 3. 去多余空白
        text = re.sub(r"\s+", " ", text).strip()

        # 4. 语言分流
        if self._is_chinese(text):
            text = self._normalize_zh(text)
        else:
            text = self._normalize_en(text)

        # 5. 归一化后再次查别名索引（"老罗" 去括号后可能是 "老罗"）
        if text in self._alias_reverse:
            return self._alias_reverse[text]

        return text

    def normalize_entity(self, name: str) -> NormalizedEntity:
        """归一化实体，返回 key + 已知别名"""
        key = self.normalize(name)
        if not key:
            return NormalizedEntity(key="", original=name or "")
        aliases = []
        if name and name != key:
            aliases.append(name)
        # 从反向索引补充别名
        for alias, mapped in self._alias_reverse.items():
            if mapped == key and alias not in aliases:
                aliases.append(alias)
        return NormalizedEntity(key=key, original=name or "", aliases=aliases)

    # ── 中文处理 ───────────────────────────────────────

    def _normalize_zh(self, text: str) -> str:
        # 去后缀
        for suffix in ZH_SUFFIXES:
            if text.endswith(suffix) and len(text) > len(suffix):
                text = text[: -len(suffix)]
                break  # 只去最长的匹配
        # 转简体
        try:
            from zhconv import convert
            text = convert(text, "zh-cn")
        except ImportError:
            pass  # zhconv 未安装时跳过（测试环境）
        return text.strip()

    # ── 英文处理 ───────────────────────────────────────

    def _normalize_en(self, text: str) -> str:
        text = text.lower().strip()
        # 去冠词
        for article in EN_ARTICLES:
            if text == article or text.startswith(article + " "):
                text = text[len(article):].strip()
        # 去标点（除连字符）
        text = re.sub(r"[.,'\"!?]", "", text).strip()
        # 去英文后缀
        for suffix in EN_SUFFIXES:
            pattern = r"\s+" + re.escape(suffix) + r"\.?$"
            if re.search(pattern, text) and len(text) > len(suffix) + 1:
                text = re.sub(pattern, "", text).strip()
                break
        # 复数词干化（简单规则：ies→y, ses→s 保留, s→去s）
        if text.endswith("ies") and len(text) > 3:
            text = text[:-3] + "y"
        elif text.endswith("s") and not text.endswith("ss") and len(text) > 3:
            text = text[:-1]
        return text.strip()

    # ── 工具 ───────────────────────────────────────────

    @staticmethod
    def _is_chinese(text: str) -> bool:
        """含中文字符视为中文"""
        return bool(re.search(r"[\u4e00-\u9fff]", text))


_default_normalizer: EntityNormalizer | None = None


def get_normalizer() -> EntityNormalizer:
    global _default_normalizer
    if _default_normalizer is None:
        _default_normalizer = EntityNormalizer()
    return _default_normalizer

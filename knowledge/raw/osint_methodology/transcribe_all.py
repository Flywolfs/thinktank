"""批量转录 B站下载的 OSINT 方法论音频片段

流程: 25s 片段 → merge_segments 合并为 ~240s → Qwen3-ASR 转录 → 保存文本
"""

import os
import sys
import json
import time
from pathlib import Path

sys.path.insert(0, "/home/zhangchi/Documents/think_tank")
from shared.crawlers.bilibili import BilibiliCLIProvider
from shared.utils import config

ASR_URL = config.QWEN3_ASR_URL.rstrip("/")
BASE = Path("/home/zhangchi/Documents/think_tank/knowledge/raw/osint_methodology")


def get_wavs(video_dir: Path) -> list[str]:
    """获取排序后的 WAV 文件列表"""
    return sorted(str(p) for p in video_dir.glob("seg_*.wav"))


def transcribe(wav_path: str) -> str:
    """调用 Qwen3-ASR 转录单个 WAV"""
    import httpx
    with open(wav_path, "rb") as f:
        r = httpx.post(
            f"{ASR_URL}/v1/audio/transcriptions",
            files={"file": ("audio.wav", f, "audio/wav")},
            data={"language": "zh"},
            timeout=300,
        )
    r.raise_for_status()
    return r.json().get("text", "")


def process_video(name: str, video_dir: Path):
    """处理一个视频: 合并 → 转录 → 保存"""
    print(f"\n{'='*50}\n处理: {name}\n{'='*50}")

    wavs = get_wavs(video_dir)
    if not wavs:
        print(f"  ❌ 没有 WAV 文件: {video_dir}")
        return

    print(f"  片段数: {len(wavs)}")

    # 合并为 ~240s 块
    merged_dir = video_dir.parent / f"{name}_merged"
    merged_dir.mkdir(exist_ok=True)
    merged = BilibiliCLIProvider.merge_segments(
        wavs, target_seconds=240, output_dir=str(merged_dir)
    )
    print(f"  合并块数: {len(merged)}")

    # 逐块转录
    full_texts = []
    for i, m in enumerate(merged):
        print(f"  [ASR] 块 {i+1}/{len(merged)}: {Path(m).name} ...", flush=True)
        try:
            text = transcribe(m)
            full_texts.append(text)
            print(f"     ✅ {len(text)} 字符")
        except Exception as e:
            print(f"     ❌ {e}")
            full_texts.append(f"[转录失败: {e}]")
        time.sleep(0.5)  # 避免请求过密

    # 保存转录文本
    transcript = "\n\n".join(full_texts)
    out_file = BASE / "transcripts" / f"{name}.txt"
    out_file.write_text(transcript, encoding="utf-8")
    print(f"  💾 已保存: {out_file} ({len(transcript)} 字符)")


if __name__ == "__main__":
    audio_dir = BASE / "audio"

    videos = [
        ("v1_osint_guide", audio_dir / "v1_osint_guide"),
        ("v2_collect_info", audio_dir / "v2_collect_info"),
        ("v3_event_review", audio_dir / "v3_event_review"),
    ]

    for name, d in videos:
        process_video(name, d)

    print("\n=== 全部完成 ===")
    for f in (BASE / "transcripts").glob("*.txt"):
        print(f"  {f.name}: {f.stat().st_size} bytes")

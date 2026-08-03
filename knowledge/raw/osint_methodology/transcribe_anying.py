"""批量转录暗影情报站 3 个视频的音频片段"""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, "/home/zhangchi/Documents/think_tank")
from shared.crawlers.bilibili import BilibiliCLIProvider
from shared.utils import config

ASR_URL = config.QWEN3_ASR_URL.rstrip("/")
BASE = Path("/home/zhangchi/Documents/think_tank/knowledge/raw/osint_methodology")


def transcribe(wav_path: str) -> str:
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
    print(f"\n{'='*50}\n处理: {name}\n{'='*50}")

    wavs = sorted(str(p) for p in video_dir.glob("seg_*.wav"))
    if not wavs:
        print(f"  ❌ 没有 WAV 文件")
        return

    print(f"  片段数: {len(wavs)}")

    merged_dir = video_dir.parent / f"{name}_merged"
    merged_dir.mkdir(exist_ok=True)
    merged = BilibiliCLIProvider.merge_segments(
        wavs, target_seconds=240, output_dir=str(merged_dir)
    )
    print(f"  合并块数: {len(merged)}")

    full_texts = []
    for i, m in enumerate(merged):
        print(f"  [ASR] 块 {i+1}/{len(merged)} ...", flush=True)
        try:
            text = transcribe(m)
            full_texts.append(text)
            print(f"     ✅ {len(text)} 字符")
        except Exception as e:
            print(f"     ❌ {e}")
            full_texts.append(f"[转录失败: {e}]")
        time.sleep(0.5)

    transcript = "\n\n".join(full_texts)
    out_file = BASE / "transcripts" / f"{name}.txt"
    out_file.write_text(transcript, encoding="utf-8")
    print(f"  💾 已保存: {out_file} ({len(transcript)} 字符)")


if __name__ == "__main__":
    audio_dir = BASE / "audio"
    videos = [
        ("v4_anying_house", audio_dir / "v4_anying_house"),
        ("v5_anying_usbase", audio_dir / "v5_anying_usbase"),
        ("v6_anying_israel", audio_dir / "v6_anying_israel"),
    ]
    for name, d in videos:
        process_video(name, d)
    print("\n=== 全部完成 ===")
    for f in (BASE / "transcripts").glob("*.txt"):
        print(f"  {f.name}: {f.stat().st_size} bytes")

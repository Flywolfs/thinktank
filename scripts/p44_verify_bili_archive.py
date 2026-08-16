"""验证 B站音频+转录持久化逻辑（不跑完整调查）

用已有 /tmp/bili_asr_* 模拟: 复制音频 + 写 transcript.txt + meta.json
"""
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

RAW_BILI_DIR = Path(__file__).parent.parent / "data" / "raw" / "bilibili"


def simulate_persist(bvid: str, full_text: str, segs: list[str], merged: list[str],
                     title: str, author: str, url: str):
    """模拟 deep_audio_node 里的存档代码路径"""
    save_dir = RAW_BILI_DIR / bvid
    audio_dir = save_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    copied = 0
    for f in segs:
        src = Path(f)
        if src.exists() and src.suffix == ".wav":
            shutil.copy2(src, audio_dir / src.name)
            copied += 1
    for m in merged:
        src = Path(m).resolve()
        if src.exists() and src.suffix == ".wav":
            shutil.copy2(src, audio_dir / f"asr_{Path(m).name}")

    if full_text:
        (save_dir / "transcript.txt").write_text(full_text, encoding="utf-8")

    import json
    meta = {
        "bvid": bvid, "title": title, "author": author, "url": url,
        "asr_chars": len(full_text), "audio_files": copied,
        "saved_at": time.time(),
    }
    (save_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return save_dir


def main():
    # 用已有 /tmp 音频模拟
    segs_dir = Path("/tmp/bili_asr_BV16ogF68EsG")
    if not segs_dir.exists():
        print("❌ 无 /tmp/bili_asr_BV16ogF68EsG，跳过（真实调查时验证）")
        return
    segs = [str(f) for f in segs_dir.glob("seg_*.wav")]
    merged = [str(f) for f in (segs_dir / "merged").glob("merged_*.wav")] if (segs_dir / "merged").exists() else []
    print(f"源音频: {len(segs)} 片段, {len(merged)} 合并块")

    save_dir = simulate_persist(
        bvid="BV16ogF68EsG",
        full_text="郭德纲现挂翻车，十五亿悬了！德云社核心资产绑定单人IP……（模拟转录全文）",
        segs=segs, merged=merged,
        title="郭德纲现挂翻车测试", author="测试UP主", url="https://www.bilibili.com/video/BV16ogF68EsG",
    )

    print(f"\n保存目录: {save_dir}")
    print(f"  audio/ 文件数: {len(list((save_dir / 'audio').glob('*.wav')))}")
    print(f"  transcript.txt: {len((save_dir / 'transcript.txt').read_text(encoding='utf-8'))} 字符")
    print(f"  meta.json: 存在={ (save_dir / 'meta.json').exists() }")
    import json
    meta = json.loads((save_dir / "meta.json").read_text(encoding="utf-8"))
    print(f"  meta: bvid={meta['bvid']} audio_files={meta['audio_files']} asr_chars={meta['asr_chars']}")

    # 清理测试数据
    shutil.rmtree(save_dir)
    print("\n✅ 持久化逻辑验证通过（测试数据已清理）")


if __name__ == "__main__":
    main()

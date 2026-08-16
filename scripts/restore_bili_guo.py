"""补存郭德纲调查转录的视频（音频 + ASR 全文 + meta）

数据来源:
- 音频: /tmp/bili_asr_{bvid}/  (旧代码只存 /tmp，重启会丢)
- 转录全文: LangGraph checkpoint 里 report.social_results[].metadata.asr_text
"""
import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

RAW_BILI_DIR = Path(__file__).parent.parent / "data" / "raw" / "bilibili"
THREAD_ID = "inv_1786873506240"  # 郭德纲调查 checkpoint


def main():
    from entity_intel.graph import get_state
    snap = get_state(THREAD_ID)
    report = snap.get("report")
    if report is None:
        print("❌ checkpoint 无 report")
        sys.exit(1)

    done = [r for r in report.social_results if r.metadata.get("asr_done")]
    print(f"待补存: {len(done)} 个视频\n")

    saved_count = 0
    for r in done:
        bvid = r.metadata.get("bvid", "")
        full_text = r.metadata.get("asr_text", "")
        if not bvid:
            continue

        save_dir = RAW_BILI_DIR / bvid
        audio_dir = save_dir / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)

        # 1. 从 /tmp 复制音频
        tmp_dir = Path(f"/tmp/bili_asr_{bvid}")
        copied = 0
        if tmp_dir.exists():
            for f in tmp_dir.glob("seg_*.wav"):
                shutil.copy2(f, audio_dir / f.name)
                copied += 1
            for m in (tmp_dir / "merged").glob("merged_*.wav"):
                src = m.resolve()
                if src.exists():
                    shutil.copy2(src, audio_dir / f"asr_{m.name}")
        else:
            print(f"  ⚠️ {bvid}: /tmp 音频目录不存在（跳过音频）")

        # 2. 写转录全文
        if full_text:
            (save_dir / "transcript.txt").write_text(full_text, encoding="utf-8")

        # 3. meta.json
        search_query = (r.metadata or {}).get("search_query", "")
        search_round = (r.metadata or {}).get("search_round", "")
        meta = {
            "bvid": bvid,
            "title": r.title,
            "author": r.author,
            "url": r.url,
            "asr_chars": len(full_text),
            "audio_files": copied,
            "saved_at": time.time(),
            "restored_from": "checkpoint+tmp",
            "investigation": {
                "job_id": "e7bef336e073",
                "entity": "郭德纲",
                "round": None,
            },
            "search_query": search_query,
            "search_round": search_round,
        }
        (save_dir / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        print(f"  ✅ {bvid} | {r.title[:35]} | 音频{copied} | 转录{len(full_text)}字")
        saved_count += 1

    print(f"\n补存完成: {saved_count}/{len(done)} 个视频 → {RAW_BILI_DIR}")


if __name__ == "__main__":
    main()

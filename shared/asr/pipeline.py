"""ASR 管道: bilix 音频下载 → ffmpeg 转码 → Qwen3-ASR 转录

前置条件:
- Qwen3-ASR Docker: localhost:8000 (docker compose ~/Documents/youtube_download/)
- bilix: uv tool install bilix
- ffmpeg: apt install ffmpeg
"""

import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


# 已知配置
ASR_URL = os.getenv("QWEN3_ASR_URL", "http://localhost:8000")
CHUNK_DURATION = 240  # 每段 4 分钟，防止 ASR token 截断


@dataclass
class ASRResult:
    """ASR 转录结果"""
    text: str               # 完整转录文本
    segments: list[dict]    # 分段信息 [{start, end, text}, ...]
    duration_seconds: float
    bvid: str = ""


class ASRPipeline:
    """B站视频 → 文本 的完整管道"""

    def __init__(self, asr_url: str = ASR_URL, work_dir: str | None = None):
        self.asr_url = asr_url.rstrip("/")
        self.work_dir = work_dir or tempfile.mkdtemp(prefix="asr_pipeline_")

    def transcribe_bilibili_video(self, bvid: str) -> ASRResult:
        """
        完整管道: bilix 下载 → ffmpeg 转码 → 切分 → ASR → 合并

        Args:
            bvid: B站视频 BV 号

        Returns:
            ASRResult 包含完整文本和分段信息
        """
        # Step 1: 下载音频
        audio_path = self._download_audio(bvid)

        # Step 2: 转码为 16kHz mono WAV
        wav_path = self._convert_to_wav(audio_path)

        # Step 3: 获取音频时长，决定是否切分
        duration = self._get_duration(wav_path)

        if duration <= CHUNK_DURATION:
            # 短音频，直接转录
            text = self._transcribe(wav_path)
            segments = [{"start": 0, "end": duration, "text": text}]
        else:
            # 长音频，切分后逐段转录
            chunk_paths = self._split_audio(wav_path, CHUNK_DURATION)
            segments = []
            for i, chunk_path in enumerate(chunk_paths):
                text = self._transcribe(chunk_path)
                segments.append({
                    "start": i * CHUNK_DURATION,
                    "end": min((i + 1) * CHUNK_DURATION, duration),
                    "text": text,
                })

        full_text = "\n".join(s["text"] for s in segments)

        # Step 4: 清理临时文件
        self._cleanup()

        return ASRResult(
            text=full_text,
            segments=segments,
            duration_seconds=duration,
            bvid=bvid,
        )

    def _download_audio(self, bvid: str) -> str:
        """通过 bilix 下载音频"""
        url = f"https://www.bilibili.com/video/{bvid}"
        output_dir = os.path.join(self.work_dir, "audio")

        result = subprocess.run(
            ["bilix", "get_video", url, "--only-audio", "--dir", output_dir],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError(f"bilix 下载失败: {result.stderr}")

        # 查找下载的文件
        for f in os.listdir(output_dir):
            if f.endswith((".m4a", ".aac", ".mp3", ".flac")):
                return os.path.join(output_dir, f)

        raise FileNotFoundError(f"未找到下载的音频文件: {result.stdout}")

    def _convert_to_wav(self, input_path: str) -> str:
        """转码为 16kHz mono WAV"""
        output_path = os.path.join(self.work_dir, "audio_16k.wav")
        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", input_path,
                "-ar", "16000",
                "-ac", "1",
                "-sample_fmt", "s16",
                output_path,
            ],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg 转码失败: {result.stderr}")
        return output_path

    def _get_duration(self, wav_path: str) -> float:
        """获取音频时长（秒）"""
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                wav_path,
            ],
            capture_output=True, text=True, timeout=10,
        )
        return float(result.stdout.strip())

    def _split_audio(self, wav_path: str, chunk_seconds: int) -> list[str]:
        """将音频切分为等长片段"""
        output_dir = os.path.join(self.work_dir, "chunks")
        os.makedirs(output_dir, exist_ok=True)

        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", wav_path,
                "-f", "segment",
                "-segment_time", str(chunk_seconds),
                "-c", "copy",
                os.path.join(output_dir, "chunk_%03d.wav"),
            ],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg 切分失败: {result.stderr}")

        chunks = sorted(Path(output_dir).glob("chunk_*.wav"))
        return [str(c) for c in chunks]

    def _transcribe(self, wav_path: str) -> str:
        """调用 Qwen3-ASR 转录"""
        import httpx

        try:
            with open(wav_path, "rb") as f:
                r = httpx.post(
                    f"{self.asr_url}/v1/audio/transcriptions",
                    files={"file": ("audio.wav", f, "audio/wav")},
                    data={"language": "zh"},
                    timeout=120,
                )
            r.raise_for_status()
            return r.json().get("text", "")
        except httpx.RequestError as e:
            raise RuntimeError(
                f"Qwen3-ASR 请求失败 ({self.asr_url}): {e}\n"
                f"请确认 Docker 服务运行中: docker ps | grep qwen"
            )

    def _cleanup(self):
        """清理临时文件"""
        import shutil
        try:
            shutil.rmtree(self.work_dir, ignore_errors=True)
        except Exception:
            pass

    @classmethod
    def health_check(cls) -> bool:
        """检查 ASR 服务是否可用"""
        import httpx
        try:
            r = httpx.get(f"{ASR_URL}/health", timeout=5)
            return r.status_code == 200
        except httpx.RequestError:
            # 尝试 /v1/models 端点
            try:
                r = httpx.get(f"{ASR_URL}/v1/models", timeout=5)
                return r.status_code == 200
            except httpx.RequestError:
                return False


# ==================== 自检 ====================
if __name__ == "__main__":
    import sys

    print("ASR Pipeline 自检")
    print("=" * 40)

    # 1. bilix
    try:
        result = subprocess.run(["bilix", "--version"], capture_output=True, text=True, timeout=5)
        print(f"  ✅ bilix: {result.stdout.strip()}")
    except FileNotFoundError:
        print(f"  ❌ bilix 未安装: uv tool install bilix")
    except Exception as e:
        print(f"  ❌ bilix: {e}")

    # 2. ffmpeg
    try:
        result = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=5)
        ver = result.stdout.split("\n")[0]
        print(f"  ✅ ffmpeg: {ver[:60]}")
    except FileNotFoundError:
        print(f"  ❌ ffmpeg 未安装: sudo apt install ffmpeg")
    except Exception as e:
        print(f"  ❌ ffmpeg: {e}")

    # 3. Qwen3-ASR
    ok = ASRPipeline.health_check()
    print(f"  {'✅' if ok else '❌'} Qwen3-ASR ({ASR_URL})")

    if not ok:
        print(f"     Docker 状态检查: docker ps | grep qwen")

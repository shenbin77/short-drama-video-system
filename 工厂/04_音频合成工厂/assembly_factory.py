# -*- coding: utf-8 -*-
"""
🎥 合成工厂 — 视频片段 + TTS配音 + 字幕 → 成品集
监听 video-factory 输出的片段包，合成最终视频

流程:
  1. 读取 episode_XXX.json (含视频片段路径+旁白文本)
  2. TTS 生成配音 (Qwen3-TTS 优先 → edge-TTS fallback)
  3. FFmpeg 合并片段 + 混音 + 字幕 → 成品
  4. 输出成品 → output/

资源占用: CPU (FFmpeg编码) + GPU (Qwen3-TTS, 可选)

用法:
  python assembly_factory.py                # 处理所有待合成集
  python assembly_factory.py --daemon       # 守护模式
  python assembly_factory.py --episode 1    # 只处理指定集
  python assembly_factory.py --tts edge     # 强制用 edge-TTS
  python assembly_factory.py --tts qwen3    # 强制用 Qwen3-TTS
"""
import argparse
import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import requests

# ── Paths ──
FACTORY_DIR = Path(__file__).parent       # 工厂/04_音频合成工厂/
ASSEMBLY_QUEUE = FACTORY_DIR / "queue"
OUTPUT_DIR = FACTORY_DIR / "output"
TEMP_DIR = FACTORY_DIR / "temp"
PROJECT_ROOT = FACTORY_DIR.parent.parent
FFMPEG = FACTORY_DIR / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
PUBLISH_QUEUE = FACTORY_DIR.parent / "05_发布工厂" / "upload_ready"  # 05_发布工厂入队

OUTPUT_DIR.mkdir(exist_ok=True)
TEMP_DIR.mkdir(exist_ok=True)
PUBLISH_QUEUE.mkdir(exist_ok=True)

# ── Novel Name (from config) ──
def _novel_name() -> str:
    cfg_path = PROJECT_ROOT / "config" / "config.yaml"
    try:
        import yaml
        data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        return data.get("novel_name", "禁蛊录")
    except Exception:
        pass
    cfg2 = FACTORY_DIR.parent / "01_小说工厂" / "config.json"
    try:
        return json.loads(cfg2.read_text(encoding="utf-8")).get("novel_name", "禁蛊录")
    except Exception:
        return "禁蛊录"

NOVEL_NAME = _novel_name()

# ── Logging ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [合成工厂] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(FACTORY_DIR / "assembly_factory.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

# ── TTS Config ──
QWEN3_TTS_URL = "http://127.0.0.1:5500"  # 本地 Qwen3-TTS 服务 (server.py --port 5500)
EDGE_TTS_VOICE = "zh-CN-YunxiNeural"      # 男声，适合沈无渊的旁白
EDGE_TTS_RATE = "+0%"


def check_qwen3_tts():
    """检查 Qwen3-TTS 本地服务是否可用"""
    try:
        r = requests.get(f"{QWEN3_TTS_URL}/healthz", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


def tts_qwen3(text, output_path, voice="narrator"):
    """Qwen3-TTS 本地服务"""
    try:
        r = requests.post(f"{QWEN3_TTS_URL}/v1/tts", json={
            "text": text,
            "voice": voice,
            "emotion": "narrative",
            "speed": 1.0,
        }, timeout=60)
        if r.status_code == 200:
            output_path.write_bytes(r.content)
            return True
    except Exception as e:
        log.warning(f"  Qwen3-TTS failed: {e}")
    return False


def tts_edge(text, output_path, voice=None, rate=None):
    """edge-TTS (Microsoft 免费)"""
    voice = voice or EDGE_TTS_VOICE
    rate = rate or EDGE_TTS_RATE
    try:
        import edge_tts
        async def _gen():
            communicate = edge_tts.Communicate(text, voice, rate=rate)
            await communicate.save(str(output_path))
        asyncio.run(_gen())
        return output_path.exists() and output_path.stat().st_size > 1000
    except ImportError:
        # Fallback: command line
        cmd = [sys.executable, "-m", "edge_tts", "--voice", voice, "--rate", rate, "--text", text, "--write-media", str(output_path)]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=30)
            return result.returncode == 0 and output_path.exists()
        except Exception as e:
            log.warning(f"  edge-TTS CLI failed: {e}")
    return False


def generate_tts(text, output_path, tts_mode="auto"):
    """生成 TTS 配音"""
    if not text or not text.strip():
        return False

    if tts_mode == "qwen3" or (tts_mode == "auto" and check_qwen3_tts()):
        if tts_qwen3(text, output_path):
            return True
        if tts_mode == "qwen3":
            return False  # 指定 qwen3 但失败

    # Fallback to edge-TTS
    return tts_edge(text, output_path)


def merge_video_segments(segment_paths, output_path):
    """FFmpeg 合并多个视频片段"""
    if not segment_paths:
        return False

    ffmpeg = str(FFMPEG) if FFMPEG.exists() else "ffmpeg"

    # 创建 concat 文件
    concat_file = TEMP_DIR / "concat_list.txt"
    with open(concat_file, "w", encoding="utf-8") as f:
        for p in segment_paths:
            f.write(f"file '{p}'\n")

    cmd = [
        ffmpeg, "-y", "-f", "concat", "-safe", "0",
        "-i", str(concat_file),
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(output_path),
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=300)
        if result.returncode == 0 and output_path.exists():
            return True
        log.warning(f"  FFmpeg merge failed: {result.stderr[:200]}")
    except Exception as e:
        log.error(f"  FFmpeg error: {e}")
    return False


def add_audio_to_video(video_path, audio_path, output_path):
    """FFmpeg 将配音混合到视频"""
    ffmpeg = str(FFMPEG) if FFMPEG.exists() else "ffmpeg"

    cmd = [
        ffmpeg, "-y",
        "-i", str(video_path),
        "-i", str(audio_path),
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-map", "0:v:0", "-map", "1:a:0",
        "-shortest",
        str(output_path),
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=120)
        return result.returncode == 0 and output_path.exists()
    except Exception as e:
        log.error(f"  FFmpeg audio mix error: {e}")
    return False


def concat_audio_files(audio_paths, output_path):
    """拼接多个音频文件"""
    ffmpeg = str(FFMPEG) if FFMPEG.exists() else "ffmpeg"

    concat_file = TEMP_DIR / "audio_concat.txt"
    with open(concat_file, "w", encoding="utf-8") as f:
        for p in audio_paths:
            f.write(f"file '{p}'\n")

    cmd = [
        ffmpeg, "-y", "-f", "concat", "-safe", "0",
        "-i", str(concat_file),
        "-c:a", "aac", "-b:a", "192k",
        str(output_path),
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=60)
        return result.returncode == 0 and output_path.exists() and output_path.stat().st_size > 500
    except Exception:
        return False


def process_episode(episode_file, tts_mode="auto"):
    """合成单集"""
    data = json.loads(episode_file.read_text(encoding="utf-8"))
    ep_num = data["episode_num"]

    # 检查是否已合成
    final_path = OUTPUT_DIR / f"{NOVEL_NAME}_第{ep_num:03d}集.mp4"
    if final_path.exists() and final_path.stat().st_size > 100000:
        log.info(f"⏭️ Episode {ep_num} already assembled")
        _push_publish_queue(ep_num, final_path, data)
        return True

    segments = data.get("segments", [])
    narrations = data.get("narration_texts", [])

    done_segs = [s for s in segments if s.get("status") == "done" and s.get("video_path")]
    if not done_segs:
        log.warning(f"⚠️ Episode {ep_num}: no completed video segments")
        return False

    log.info(f"🎥 合成 Episode {ep_num}: {len(done_segs)} segments, {len(narrations)} narrations")

    ep_temp = TEMP_DIR / f"ep_{ep_num:03d}"
    ep_temp.mkdir(exist_ok=True)

    # Step 1: Merge video segments
    video_paths = [Path(s["video_path"]) for s in done_segs if Path(s["video_path"]).exists()]
    merged_video = ep_temp / "merged.mp4"

    if len(video_paths) == 1:
        shutil.copy2(video_paths[0], merged_video)
    elif len(video_paths) > 1:
        if not merge_video_segments(video_paths, merged_video):
            log.error(f"  ❌ Video merge failed")
            return False
    else:
        log.error(f"  ❌ No valid video files found")
        return False

    log.info(f"  📼 Video merged: {merged_video.stat().st_size // 1024}KB")

    # Step 2: Generate TTS for narrations
    audio_parts = []
    for i, text in enumerate(narrations):
        if not text or not text.strip():
            continue
        audio_path = ep_temp / f"narration_{i+1:02d}.mp3"
        if audio_path.exists() and audio_path.stat().st_size > 500:
            audio_parts.append(audio_path)
            continue

        log.info(f"  🔊 TTS {i+1}/{len(narrations)}: {text[:30]}...")
        if generate_tts(text, audio_path, tts_mode):
            audio_parts.append(audio_path)
            log.info(f"  ✅ TTS done")
        else:
            log.warning(f"  ⚠️ TTS failed for segment {i+1}")

    # Step 3: Combine audio + video
    if audio_parts:
        # 拼接所有音频
        full_audio = ep_temp / "full_narration.m4a"
        if len(audio_parts) == 1:
            shutil.copy2(audio_parts[0], full_audio)
        else:
            concat_audio_files(audio_parts, full_audio)

        if full_audio.exists() and full_audio.stat().st_size > 500:
            log.info(f"  🎙️ Adding narration to video...")
            if add_audio_to_video(merged_video, full_audio, final_path):
                log.info(f"  ✅ 成品: {final_path.name} ({final_path.stat().st_size // 1024}KB)")
                _push_publish_queue(ep_num, final_path, data)
                return True
            else:
                log.warning(f"  ⚠️ Audio mix failed, using video without narration")

    # Fallback: 没有配音就直接用纯视频
    if not final_path.exists():
        shutil.copy2(merged_video, final_path)
        log.info(f"  📼 成品(无配音): {final_path.name}")

    if final_path.exists():
        _push_publish_queue(ep_num, final_path, data)

    return True


def _push_publish_queue(ep_num: int, video_path: Path, data: dict):
    """将成品推送到05_发布工厂队列"""
    job = PUBLISH_QUEUE / f"episode_{ep_num:03d}.json"
    if job.exists():
        return
    payload = {
        "episode_num": ep_num,
        "novel": NOVEL_NAME,
        "video_path": str(video_path),
        "title": f"{NOVEL_NAME} 第{ep_num}集",
        "description": data.get("chapter_summary", ""),
        "tags": ["仙侠", "短剧", NOVEL_NAME],
        "status": "ready",
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    job.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info(f"  📤 推送发布队列: {job.name}")


def main():
    parser = argparse.ArgumentParser(description="🎥 合成工厂")
    parser.add_argument("--daemon", action="store_true", help="守护模式")
    parser.add_argument("--episode", type=int, default=0, help="只处理指定集")
    parser.add_argument("--tts", default="auto", choices=["auto", "qwen3", "edge"], help="TTS引擎")
    args = parser.parse_args()

    # 检查 TTS 可用性
    qwen3_ok = check_qwen3_tts()
    log.info(f"🎥 合成工厂启动 | TTS: {args.tts} (Qwen3: {'✅' if qwen3_ok else '❌'}, edge-TTS: ✅)")
    log.info(f"  FFmpeg: {'✅' if FFMPEG.exists() else '⚠️ using system ffmpeg'}")

    while True:
        episode_files = sorted(ASSEMBLY_QUEUE.glob("episode_*.json"))
        if args.episode > 0:
            episode_files = [f for f in episode_files if f"_{args.episode:03d}" in f.name]

        pending = []
        for f in episode_files:
            data = json.loads(f.read_text(encoding="utf-8"))
            ep = data["episode_num"]
            # 跳过视频工厂标记为 failed 的集（所有 segment 均失败，重试无意义）
            if data.get("status") == "failed":
                continue
            final = OUTPUT_DIR / f"{NOVEL_NAME}_第{ep:03d}集.mp4"
            if not final.exists() or final.stat().st_size < 100000:
                pending.append(f)

        if pending:
            log.info(f"📋 发现 {len(pending)} 集待合成")
            for ef in pending:
                process_episode(ef, tts_mode=args.tts)
        else:
            if not args.daemon:
                log.info("✅ 所有集已合成")
                break
            log.info("⏳ 等待60秒...")

        if not args.daemon:
            break
        time.sleep(60)


if __name__ == "__main__":
    main()

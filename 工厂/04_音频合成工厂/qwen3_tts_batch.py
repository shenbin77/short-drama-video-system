# -*- coding: utf-8 -*-
"""
Qwen3-TTS 批量旁白生成脚本

用法 (需在 qwen3tts 环境中运行):
  conda activate qwen3tts
  python scripts/qwen3_tts_batch.py --script-id 8
  python scripts/qwen3_tts_batch.py --script-id 8 --test         # 只生成1段测试
  python scripts/qwen3_tts_batch.py --script-id 8 --mode narration  # 纯旁白

模型位置:
  - E:/models/Qwen3-TTS-Tokenizer-12Hz
  - E:/models/Qwen3-TTS-12Hz-1.7B-CustomVoice
  - E:/models/Qwen3-TTS-12Hz-1.7B-Base (声音克隆用)

VRAM: ~4GB (1.7B BF16)
"""

import argparse
import json
import logging
import os
import re
import sqlite3
import sys
import time
import wave
from pathlib import Path

import numpy as np
import soundfile as sf

# torch 延迟加载（移到 load_tts_model 内部，统一错误提示）
_torch_available = False
try:
    import torch
    _torch_available = True
except ImportError:
    _torch_available = False

SCRIPT_DIR = Path(__file__).resolve().parent
LOG_DIR = SCRIPT_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(str(LOG_DIR / "qwen3_tts_batch.log"), encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

# ── 路径常量 ─────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
# DB path: Windows APPDATA first, WSL fallback
_appdata = os.environ.get("APPDATA", "")
if _appdata:
    DB_PATH = os.path.join(_appdata, "toonflow-app", "db.sqlite")
else:
    DB_PATH = str(Path.home() / ".local" / "share" / "toonflow-app" / "db.sqlite")
OUTPUT_DIR = PROJECT_ROOT / "Layer_3_Audio" / "tts"

# Qwen3-TTS 模型路径
TOKENIZER_PATH = "E:/models/Qwen3-TTS-Tokenizer-12Hz"
CUSTOM_VOICE_MODEL = "E:/models/Qwen3-TTS-12Hz-1.7B-CustomVoice"
BASE_MODEL = "E:/models/Qwen3-TTS-12Hz-1.7B-Base"

# ── 角色→声音映射 ────────────────────────────────────────
# Qwen3-TTS CustomVoice 内置 speaker:
#   Chelsie, Ethan, Cherry, Serena, ...
# instruct 控制语气
VOICE_MAP = {
    "旁白":     {"speaker": "Ethan",   "instruct": "用沉稳低沉的声音朗读，像电影旁白"},
    "沈无渊":   {"speaker": "Ethan",   "instruct": "用冷峻内敛的声音说话，年轻男性"},
    "秦墨":     {"speaker": "Ethan",   "instruct": "用冷厉严肃的声音说话，中年男性执法者"},
    "苏晚晴":   {"speaker": "Serena",  "instruct": "用轻柔飘逸的声音说话，年轻女性"},
    "陆九幽":   {"speaker": "Ethan",   "instruct": "用低沉圆滑的声音说话，中年富态商人"},
    "月姬":     {"speaker": "Cherry",  "instruct": "用阴冷妖媚的声音说话，女性反派"},
    "玄机老人": {"speaker": "Ethan",   "instruct": "用苍老缓慢的声音说话，老年男性"},
    "厉长老":   {"speaker": "Ethan",   "instruct": "用沙哑阴森的声音说话，反派老者"},
}

DEFAULT_VOICE = {"speaker": "Ethan", "instruct": "用自然平缓的声音说话"}


def parse_script_lines(content, mode="full"):
    """从剧本内容提取旁白和对白"""
    lines = []
    for raw in content.split("\n"):
        raw = raw.strip()
        if not raw:
            continue

        # 对白: "角色名（动作）：台词" 或 "角色名：台词"
        m = re.match(r'^([\u4e00-\u9fff]{1,6})（([^）]*)）[：:](.+)$', raw)
        if not m:
            m_simple = re.match(r'^([\u4e00-\u9fff]{1,6})[：:](.+)$', raw)
            if m_simple:
                speaker = m_simple.group(1)
                dialogue = m_simple.group(2).strip()
                if len(dialogue) >= 2:
                    lines.append((speaker, dialogue, "dialogue"))
                continue

        if m:
            speaker = m.group(1)
            dialogue = m.group(3).strip()
            if len(dialogue) >= 2:
                lines.append((speaker, dialogue, "dialogue"))
            continue

        # 非对白 → 旁白
        if raw.startswith("【") or raw.startswith("##") or raw.startswith("---"):
            continue
        if raw.startswith("※") or raw.startswith("$"):
            continue
        if re.match(r'^[\[（(【]?(音效|BGM|镜头|画面|特效|转场|黑屏|fade)', raw):
            continue
        if re.match(r'^△\s*(切[：:]?|插入[：:]?|转[：:]?)\s*$', raw):
            continue

        narr_text = re.sub(r'^△\s*(大远景|远景|中景|近景|特写|全景|俯拍|仰拍|侧拍|平拍|推镜|拉镜)[^，,]*[，,]\s*', '', raw)
        narr_text = re.sub(r'^△\s*', '', narr_text)
        narr_text = re.sub(r'^画[中左右上下]\s*[，,]\s*', '', narr_text)
        narr_text = narr_text.strip()
        if len(narr_text) < 8:
            continue
        lines.append(("旁白", narr_text, "narration"))

    if mode == "narration":
        lines = [l for l in lines if l[2] == "narration"]
    elif mode == "dialogue":
        lines = [l for l in lines if l[2] == "dialogue"]
    return lines


def get_script_content(script_id):
    """从 ToonFlow DB 读剧本"""
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT name, content FROM t_script WHERE id=?", (script_id,)
    ).fetchone()
    conn.close()
    if not row:
        raise ValueError(f"未找到 script_id={script_id}")
    return row[0], row[1] or ""


def load_tts_model():
    """加载 Qwen3-TTS 模型"""
    try:
        from qwen_tts import Qwen3TTSModel

        logger.info("Loading Qwen3-TTS CustomVoice model...")
        model = Qwen3TTSModel.from_pretrained(
            CUSTOM_VOICE_MODEL,
            tokenizer=TOKENIZER_PATH,
            device_map="cuda:0",
            dtype=torch.bfloat16,
        )
        logger.info("Qwen3-TTS loaded on GPU")
        return model
    except ImportError:
        logger.error("qwen-tts not installed. Run: pip install qwen-tts")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Failed to load Qwen3-TTS: {e}")
        sys.exit(1)


def generate_audio(model, text, speaker, instruct, output_path):
    """Generate a single audio segment"""
    try:
        wavs, sr = model.generate_custom_voice(
            text=text,
            language="Chinese",
            speaker=speaker,
            instruct=instruct,
        )
        # Save as wav
        wav_data = wavs[0].cpu().numpy()
        sf.write(output_path, wav_data, sr)
        duration = len(wav_data) / sr
        return duration
    except Exception as e:
        logger.error(f"TTS generation failed: {e}")
        return 0


def main():
    parser = argparse.ArgumentParser(description="Qwen3-TTS Batch Narration")
    parser.add_argument("--script-id", type=int, required=True, help="ToonFlow script ID")
    parser.add_argument("--mode", default="full", choices=["full", "narration", "dialogue"])
    parser.add_argument("--test", action="store_true", help="Only generate 1 segment")
    parser.add_argument("--output-dir", default=None, help="Override output dir")
    args = parser.parse_args()

    out_dir = Path(args.output_dir) if args.output_dir else OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    logger.info("=" * 60)
    logger.info("Qwen3-TTS Batch Narration")
    logger.info(f"  Script ID: {args.script_id}")
    logger.info(f"  Mode: {args.mode}")
    logger.info(f"  Output: {out_dir}")
    logger.info("=" * 60)

    # 1. Read script
    script_name, content = get_script_content(args.script_id)
    logger.info(f"Script: {script_name} ({len(content)} chars)")

    # 2. Parse lines
    parsed = parse_script_lines(content, args.mode)
    if not parsed:
        logger.warning("No lines to generate")
        return

    n_d = sum(1 for p in parsed if p[2] == "dialogue")
    n_n = sum(1 for p in parsed if p[2] == "narration")
    logger.info(f"Parsed: {n_d} dialogue, {n_n} narration")

    if args.test:
        parsed = parsed[:1]
        logger.info("TEST MODE: only 1 segment")

    # 3. Load model
    model = load_tts_model()

    # 4. Generate
    results = []
    total_dur = 0
    for i, (speaker, text, line_type) in enumerate(parsed):
        voice_cfg = VOICE_MAP.get(speaker, DEFAULT_VOICE)
        out_path = str(out_dir / f"tts_{i:04d}_{speaker}.wav")

        logger.info(f"[{i+1}/{len(parsed)}] {speaker}: {text[:40]}...")
        t0 = time.time()

        dur = generate_audio(
            model, text,
            speaker=voice_cfg["speaker"],
            instruct=voice_cfg["instruct"],
            output_path=out_path,
        )

        elapsed = time.time() - t0
        total_dur += dur
        results.append({
            "index": i,
            "speaker": speaker,
            "text": text,
            "duration": dur,
            "path": out_path,
            "line_type": line_type,
        })
        logger.info(f"  -> {dur:.1f}s audio in {elapsed:.1f}s")

    # 5. Save manifest
    manifest_path = out_dir / "tts_manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    # 6. Summary
    logger.info("\n" + "=" * 60)
    logger.info(f"Generated {len(results)} audio segments")
    logger.info(f"Total audio: {total_dur:.1f}s")
    logger.info(f"Manifest: {manifest_path}")
    logger.info(f"Output: {out_dir}")
    logger.info("=" * 60)

    # Free GPU
    del model
    torch.cuda.empty_cache()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
yolo_storyboard.py — WSL端极速分镜打包
只做: 1. 读取chapter JSON → 2. 生成分镜大纲(outline) → 3. 打包episode到视频队列
跳过图片生成（等Windows端补）
"""

import sys, os, json, logging, time, shutil
from pathlib import Path

# 路径设置
BASE = Path("/mnt/e/视频项目") / "工厂"
QUEUE_DIR = BASE / "01_小说工厂" / "queue"
OUTPUT_DIR = BASE / "02_图片工厂" / "output"
NOVEL_NAME = "禁蛊录"
VIDEO_QUEUE = BASE / "03_视频工厂" / "queue"

# 日志
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("yolo_storyboard")

# 导入storyboard核心函数
sys.path.insert(0, str(BASE / "02_图片工厂"))
from storyboard_factory import (
    load_characters,
    generate_storyboard_outline,
    deepseek_call,
    NOVEL_NAME as _N,
    OUTPUT_DIR as _O,
    VIDEO_QUEUE as _VQ,
)


def main():
    # 读取角色配置
    characters = load_characters()
    log.info(f"📋 角色配置加载: {len(characters)} 个角色")

    # 扫描待处理章节
    chapter_files = sorted(QUEUE_DIR.glob("chapter_*.json"))
    pending = []
    for cf in chapter_files:
        data = json.loads(cf.read_text(encoding="utf-8"))
        ch = data["chapter_num"]
        out = VIDEO_QUEUE / f"episode_{ch:03d}.json"
        if not out.exists():
            pending.append(cf)
        else:
            try:
                ep = json.loads(out.read_text(encoding="utf-8"))
                # 如果episode已有图片则跳过
                all_ok = all(
                    s.get("image_path") and Path(s["image_path"]).exists()
                    for s in ep.get("shots", [])
                )
                if not all_ok:
                    pending.append(cf)
            except:
                pending.append(cf)

    if not pending:
        log.info("✅ 所有章节已处理完毕，无需操作")
        return

    log.info(f"📋 发现 {len(pending)} 个待处理章节:")
    for p in pending:
        log.info(f"   - {p.stem}")

    # 逐个处理
    for cf in pending:
        data = json.loads(cf.read_text(encoding="utf-8"))
        ch = data["chapter_num"]
        chapter_text = data["text"]

        log.info(f"🎨 处理第{ch}章 ({data['char_count']}字, {data['segment_count']}段)")

        # 目录
        episode_dir = OUTPUT_DIR / NOVEL_NAME / f"episode_{ch:03d}"
        episode_dir.mkdir(parents=True, exist_ok=True)

        # Step 1: 分镜大纲（优先缓存）
        outline_cache = episode_dir / "outline.json"
        if outline_cache.exists():
            shots = json.loads(outline_cache.read_text(encoding="utf-8"))
            log.info(f"  📋 分镜大纲 (缓存): {len(shots)}个镜头")
        else:
            log.info(f"  🔄 调用DeepSeek生成分镜大纲...")
            t0 = time.time()
            shots = generate_storyboard_outline(chapter_text, ch, characters)
            elapsed = time.time() - t0
            if not shots:
                log.error(f"  ❌ 第{ch}章分镜大纲生成失败，跳过")
                continue
            outline_cache.write_text(
                json.dumps(shots, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            log.info(f"  ✅ 分镜大纲: {len(shots)}个镜头 ({elapsed:.1f}s, 已缓存)")

        # Step 2: 扫描已有图片，没有的标记为None
        for i, shot in enumerate(shots):
            img_path = episode_dir / f"shot_{i+1:02d}.jpg"
            if img_path.exists() and img_path.stat().st_size > 10000:
                shot["image_path"] = str(img_path)
            else:
                shot["image_path"] = None

        # Step 3: 打包到视频队列
        episode_pkg = {
            "novel_name": NOVEL_NAME,
            "episode_num": ch,
            "chapter_num": ch,
            "total_shots": len(shots),
            "shots": shots,
            "generated": False,
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        out_path = VIDEO_QUEUE / f"episode_{ch:03d}.json"
        out_path.write_text(
            json.dumps(episode_pkg, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        log.info(f"  📦 打包完成 → {out_path.name}")

    # 统计
    total = len(chapter_files)
    packed = sum(1 for cf in chapter_files if (VIDEO_QUEUE / f"episode_{json.loads(cf.read_text(encoding='utf-8'))['chapter_num']:03d}.json").exists())
    log.info(f"🎉 完成! 已打包 {packed}/{total} 集")
    log.info(f"   视频队列: {VIDEO_QUEUE}")
    log.info(f"   下一步: Windows端启动补图片+视频+音频")


if __name__ == "__main__":
    main()

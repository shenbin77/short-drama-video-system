# -*- coding: utf-8 -*-
"""
🎯 主调度器 — 6工厂统一管理
启动/监控/重启所有工厂，实时显示队列状态

用法:
  python orchestrator.py              # 启动所有工厂
  python orchestrator.py --status     # 仅查看状态
  python orchestrator.py --factory novel  # 只启动指定工厂
  支持工厂名: novel/storyboard/video/assembly/ops/all
"""
import argparse
import json
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

SERVICES_DIR = Path(__file__).parent
PROJECT_ROOT = SERVICES_DIR.parent

FACTORIES = {
    "novel": {
        "name": "📖 01_小说工厂",
        "script": SERVICES_DIR / "01_小说工厂" / "novel_factory.py",
        "args": ["--chapters", "10"],
        "daemon_args": ["--daemon"],
        "queue_dir": SERVICES_DIR / "01_小说工厂" / "queue",
        "output_dir": SERVICES_DIR / "01_小说工厂" / "output",
    },
    "storyboard": {
        "name": "🎨 02_图片工厂",
        "script": SERVICES_DIR / "02_图片工厂" / "storyboard_factory.py",
        "args": [],
        "daemon_args": ["--daemon"],
        "queue_dir": SERVICES_DIR / "03_视频工厂" / "queue",
        "output_dir": SERVICES_DIR / "02_图片工厂" / "output",
    },
    "video": {
        "name": "� 03_视频工厂",
        "script": SERVICES_DIR / "03_视频工厂" / "video_factory.py",
        "args": [],
        "daemon_args": ["--daemon"],
        "queue_dir": SERVICES_DIR / "04_音频合成工厂" / "queue",
        "output_dir": SERVICES_DIR / "03_视频工厂" / "output",
    },
    "assembly": {
        "name": "🎵 04_音频合成工厂",
        "script": SERVICES_DIR / "04_音频合成工厂" / "assembly_factory.py",
        "args": [],
        "daemon_args": ["--daemon", "--tts", "auto"],
        "queue_dir": SERVICES_DIR / "04_音频合成工厂" / "output",
        "output_dir": SERVICES_DIR / "04_音频合成工厂" / "output",
    },
    "ops": {
        "name": "📊 06_运营工厂",
        "script": SERVICES_DIR / "06_运营工厂" / "ops_factory.py",
        "args": [],
        "daemon_args": ["--daemon"],
        "queue_dir": SERVICES_DIR / "05_发布工厂" / "upload_ready",
        "output_dir": SERVICES_DIR / "05_发布工厂" / "published",
    },
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [调度器] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(SERVICES_DIR / "orchestrator.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

processes = {}


def get_queue_status():
    """获取各队列状态"""
    status = {}
    dirs = {
        "novel_queue": SERVICES_DIR / "01_小说工厂" / "queue",
        "novel_output": SERVICES_DIR / "01_小说工厂" / "output",
        "storyboard_output": SERVICES_DIR / "02_图片工厂" / "output",
        "video_queue": SERVICES_DIR / "03_视频工厂" / "queue",
        "video_output": SERVICES_DIR / "03_视频工厂" / "output",
        "assembly_queue": SERVICES_DIR / "04_音频合成工厂" / "queue",
        "final_output": SERVICES_DIR / "04_音频合成工厂" / "output",
        "publish_ready": SERVICES_DIR / "05_发布工厂" / "upload_ready",
        "published": SERVICES_DIR / "05_发布工厂" / "published",
    }
    for name, d in dirs.items():
        if d.exists():
            if name == "final_output":
                files = list(d.glob("*.mp4"))
            elif name.endswith("_output"):
                files = list(d.glob("*"))
            else:
                files = list(d.glob("*.json"))
            status[name] = len(files)
        else:
            status[name] = 0
    return status


def print_status():
    """打印状态面板"""
    s = get_queue_status()
    print("\n" + "=" * 60)
    print("  🎯 24h 全自动产线状态面板")
    print("=" * 60)
    print(f"  📖 01_小说工厂   | 已写: {s.get('novel_output',0):3d} 章 | 队列: {s.get('novel_queue',0):3d}")
    print(f"  🎨 02_图片工厂   | 已出: {s.get('storyboard_output',0):3d} 集 | → 视频队列: {s.get('video_queue',0):3d}")
    print(f"  🎬 03_视频工厂   | 已出: {s.get('video_output',0):3d} 集 | → 合成队列: {s.get('assembly_queue',0):3d}")
    print(f"  � 合成工厂   | 成品: {s.get('final_output',0):3d} 集 | → 发布队列: {s.get('publish_ready',0):3d}")
    print(f"  📊 06_运营工厂   | 已发: {s.get('published',0):3d} 集")
    print("=" * 60)

    # Process status
    for key, info in FACTORIES.items():
        proc = processes.get(key)
        if proc and proc.poll() is None:
            print(f"  {info['name']}: 🟢 运行中 (PID {proc.pid})")
        elif proc:
            print(f"  {info['name']}: 🔴 已停止 (exit {proc.returncode})")
        else:
            print(f"  {info['name']}: ⚪ 未启动")
    print()


def start_factory(key, daemon=True):
    """启动单个工厂"""
    info = FACTORIES[key]
    args = info["daemon_args"] if daemon else info["args"]
    cmd = [sys.executable, str(info["script"])] + args

    log.info(f"启动 {info['name']}: {' '.join(cmd)}")

    proc = subprocess.Popen(
        cmd,
        cwd=str(info["script"].parent),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    processes[key] = proc
    return proc


def stop_all():
    """停止所有工厂"""
    for key, proc in processes.items():
        if proc and proc.poll() is None:
            log.info(f"停止 {FACTORIES[key]['name']} (PID {proc.pid})")
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


def main():
    parser = argparse.ArgumentParser(description="🎯 禁蛊录产线调度器")
    parser.add_argument("--status", action="store_true", help="只看状态")
    parser.add_argument("--factory", default="all", help="启动指定工厂: novel/storyboard/video/assembly/ops/all")
    parser.add_argument("--no-daemon", action="store_true", help="非守护模式(跑完即停)")
    args = parser.parse_args()

    if args.status:
        print_status()
        return

    log.info("🎯 禁蛊录 24h 产线启动")
    log.info(f"  模式: {'单次' if args.no_daemon else '守护(持续运行)'}")

    # 注册退出信号
    signal.signal(signal.SIGINT, lambda *a: (stop_all(), sys.exit(0)))
    signal.signal(signal.SIGTERM, lambda *a: (stop_all(), sys.exit(0)))

    daemon = not args.no_daemon
    factories_to_start = list(FACTORIES.keys()) if args.factory == "all" else [args.factory]

    # 按顺序启动 (上游先启动)
    order = ["novel", "storyboard", "video", "assembly", "ops"]
    for key in order:
        if key in factories_to_start:
            start_factory(key, daemon=daemon)
            time.sleep(3)  # 间隔启动

    # 监控循环
    try:
        while True:
            print_status()

            # 检查并重启挂掉的工厂
            for key in factories_to_start:
                proc = processes.get(key)
                if proc and proc.poll() is not None and daemon:
                    log.warning(f"  {FACTORIES[key]['name']} 意外退出，重启...")
                    start_factory(key, daemon=True)

            # 检查是否全部完成 (非守护模式)
            if not daemon:
                all_done = all(
                    processes.get(k) and processes[k].poll() is not None
                    for k in factories_to_start
                )
                if all_done:
                    log.info("所有工厂已完成")
                    break

            time.sleep(30)
    except KeyboardInterrupt:
        log.info("收到退出信号...")
    finally:
        stop_all()
        print_status()


if __name__ == "__main__":
    main()

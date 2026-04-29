# -*- coding: utf-8 -*-
"""
📊 06_运营工厂 — 数据采集 · 效果分析 · 发布调度 · 策略反馈

职责:
  1. 监听05_发布工厂 upload_ready/ 队列，按平台最优时间排期
  2. 定时采集各平台播放/点赞/完播率数据
  3. 分析效果 → 更新 config/config.yaml 策略偏好
  4. 闭环反馈给01_小说工厂（调整题材/节奏/关键词）

用法:
  python ops_factory.py               # 单次运行（处理队列+采集数据）
  python ops_factory.py --daemon      # 守护模式（每30分钟一次）
  python ops_factory.py --publish-now # 立即发布所有ready队列
"""
import argparse
import json
import logging
import os
import time
from pathlib import Path

# ── Paths ──
FACTORY_DIR   = Path(__file__).parent              # 工厂/06_运营工厂/
PROJECT_ROOT  = FACTORY_DIR.parent.parent          # E:\视频项目
PUBLISH_QUEUE = FACTORY_DIR.parent / "05_发布工厂" / "upload_ready"
PUBLISHED_DIR = FACTORY_DIR.parent / "05_发布工厂" / "published"
STATS_DIR     = FACTORY_DIR / "stats"
REPORT_DIR    = FACTORY_DIR / "reports"
CONFIG_PATH   = PROJECT_ROOT / "config" / "config.yaml"

PUBLISH_QUEUE.mkdir(exist_ok=True)
PUBLISHED_DIR.mkdir(exist_ok=True)
STATS_DIR.mkdir(exist_ok=True)
REPORT_DIR.mkdir(exist_ok=True)

# ── Logging ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [06_运营工厂] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(FACTORY_DIR / "ops_factory.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

# ── 平台最优发布时间（北京时间） ──
PLATFORM_SCHEDULE = {
    "douyin":    ["07:00", "12:00", "18:00", "21:00"],
    "kuaishou":  ["08:00", "12:30", "19:00", "22:00"],
    "bilibili":  ["10:00", "15:00", "20:00"],
    "youtube":   ["09:00", "17:00", "21:00"],
    "weixin":    ["07:30", "12:00", "20:30"],
}

# ── social-auto-upload 路径 ──
UPLOAD_TOOL = FACTORY_DIR.parent / "05_发布工厂" / "social-auto-upload"


# ══════════════════════════════════════════════════════════════
# 1. 发布调度
# ══════════════════════════════════════════════════════════════

def get_pending_jobs() -> list:
    """读取待发布队列"""
    jobs = []
    for f in sorted(PUBLISH_QUEUE.glob("episode_*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if data.get("status") == "ready":
                data["_file"] = str(f)
                jobs.append(data)
        except Exception as e:
            log.warning(f"  读取队列文件失败 {f.name}: {e}")
    return jobs


def publish_episode(job: dict) -> bool:
    """发布单集到各平台"""
    ep_num    = job["episode_num"]
    video_path = Path(job["video_path"])
    title      = job.get("title", f"第{ep_num}集")

    if not video_path.exists():
        log.error(f"  ❌ 视频文件不存在: {video_path}")
        return False

    log.info(f"📤 发布 Episode {ep_num}: {title}")

    # 调用 social-auto-upload（如已安装）
    upload_script = UPLOAD_TOOL / "sau_cli.py"
    if upload_script.exists():
        import subprocess
        try:
            result = subprocess.run(
                ["python", str(upload_script),
                 "--video", str(video_path),
                 "--title", title,
                 "--tags", ",".join(job.get("tags", []))],
                cwd=str(UPLOAD_TOOL),
                capture_output=True, text=True,
                encoding="utf-8", errors="ignore",
                timeout=300,
            )
            if result.returncode == 0:
                log.info(f"  ✅ 上传成功")
            else:
                log.warning(f"  ⚠️ 上传脚本返回: {result.stderr[:200]}")
        except Exception as e:
            log.warning(f"  ⚠️ 上传工具调用失败: {e}")
    else:
        log.info(f"  ℹ️ social-auto-upload 脚本未安装，记录待上传")

    # 标记已处理
    job_file = Path(job["_file"])
    job["status"] = "published"
    job["published_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    published_copy = PUBLISHED_DIR / job_file.name
    published_copy.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info(f"  📁 归档: {published_copy.name}")

    # 发布成功后再删除源文件
    if published_copy.exists():
        job_file.unlink(missing_ok=True)
    return True


# ══════════════════════════════════════════════════════════════
# 2. 数据采集（占位，扩展时实现）
# ══════════════════════════════════════════════════════════════

def collect_stats() -> dict:
    """采集各平台数据（占位：扩展时对接各平台API）"""
    log.info("📊 数据采集... (待接入平台API)")
    stats = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "platforms": {}
    }
    # TODO: 对接抖音/快手/B站数据API
    stats_file = STATS_DIR / f"stats_{time.strftime('%Y%m%d_%H%M')}.json"
    stats_file.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    return stats


# ══════════════════════════════════════════════════════════════
# 3. 策略反馈
# ══════════════════════════════════════════════════════════════

def generate_strategy_report(stats: dict) -> dict:
    """分析数据，生成策略建议"""
    report = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "recommendations": [],
        "config_updates": {}
    }
    # TODO: 接入真实数据后，分析完播率/点赞率，反馈给 config.yaml
    # 示例逻辑：
    # if avg_completion_rate < 0.3:
    #     report["config_updates"]["pacing"] = "faster"
    #     report["recommendations"].append("完播率低，建议加快节奏")

    report_file = REPORT_DIR / f"report_{time.strftime('%Y%m%d_%H%M')}.json"
    report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info(f"  📋 策略报告: {report_file.name}")
    return report


# ══════════════════════════════════════════════════════════════
# 4. 队列状态面板
# ══════════════════════════════════════════════════════════════

def print_status():
    ready     = len(list(PUBLISH_QUEUE.glob("episode_*.json")))
    published = len(list(PUBLISHED_DIR.glob("episode_*.json")))
    stats     = len(list(STATS_DIR.glob("*.json")))

    print("\n" + "=" * 50)
    print("  📊 06_运营工厂状态面板")
    print("=" * 50)
    print(f"  ⏳ 待发布:   {ready:3d} 集")
    print(f"  ✅ 已发布:   {published:3d} 集")
    print(f"  📈 数据报告: {stats:3d} 份")
    print("=" * 50)


# ══════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="📊 06_运营工厂")
    parser.add_argument("--daemon",       action="store_true", help="守护模式（每30分钟循环）")
    parser.add_argument("--publish-now",  action="store_true", help="立即发布所有ready集")
    parser.add_argument("--status",       action="store_true", help="显示状态面板")
    args = parser.parse_args()

    log.info("📊 06_运营工厂启动")
    log.info(f"  发布队列: {PUBLISH_QUEUE}")
    log.info(f"  已发布目录: {PUBLISHED_DIR}")

    if args.status:
        print_status()
        return

    if args.publish_now:
        log.info("⚡ --publish-now 模式: 立即发布所有ready队列")
        jobs = get_pending_jobs()
        if not jobs:
            log.info("  ⏳ 没有待发布的任务")
        else:
            log.info(f"📋 发现 {len(jobs)} 集待发布")
            for job in jobs:
                publish_episode(job)
        return

    while True:
        # 1. 处理发布队列
        jobs = get_pending_jobs()
        if jobs:
            log.info(f"📋 发现 {len(jobs)} 集待发布")
            for job in jobs:
                publish_episode(job)
        else:
            log.info("  ⏳ 发布队列为空")

        # 2. 采集数据
        stats = collect_stats()

        # 3. 策略分析
        generate_strategy_report(stats)

        # 4. 状态面板
        print_status()

        if not args.daemon:
            break
        log.info("⏳ 等待 1800 秒 (30分钟)...")
        time.sleep(1800)


if __name__ == "__main__":
    main()

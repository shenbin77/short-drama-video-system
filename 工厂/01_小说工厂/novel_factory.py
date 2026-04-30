# -*- coding: utf-8 -*-
"""
📖 小说工厂 — 禁蛊录自动批量生成
队列驱动: 生成章节 → output/ → 下游分镜工厂监听

用法:
  python novel_factory.py                    # 默认生成10章
  python novel_factory.py --chapters 20      # 生成20章
  python novel_factory.py --start 5          # 从第5章开始
  python novel_factory.py --daemon           # 守护模式(持续运行)
"""
import argparse
import json
import logging
import os
import subprocess
import sys
import signal
import time
from pathlib import Path

import requests
import yaml

# ── Paths ──
FACTORY_DIR = Path(__file__).parent       # 工厂/小说工厂/
OUTPUT_BASE = FACTORY_DIR / "output"      # output/{novel_name}/ 延迟创建
QUEUE_DIR = FACTORY_DIR / "queue"
CONFIG_PATH = FACTORY_DIR / "config.json"
PROGRESS_PATH = FACTORY_DIR / "progress.json"
PROJECT_ROOT = FACTORY_DIR.parent.parent  # E:\\视频项目

OUTPUT_BASE.mkdir(exist_ok=True)
QUEUE_DIR.mkdir(exist_ok=True)


def _get_output_dir(novel_name: str) -> Path:
    """output/{novel_name}/ 子目录，按小说名隔离"""
    d = OUTPUT_BASE / novel_name
    d.mkdir(parents=True, exist_ok=True)
    return d

# AI_NovelGenerator 开源工具路径 (与本工厂同目录)
AI_NOVEL_ENGINE = FACTORY_DIR / "AI_NovelGenerator"

# ── Logging ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [小说工厂] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(FACTORY_DIR / "novel_factory.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

# ── DeepSeek API ──
API_URL = "https://api.deepseek.com/v1/chat/completions"
API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")


def load_config():
    """加载小说配置"""
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    # 默认配置
    cfg = {
        "novel_name": "禁蛊录",
        "protagonist": "沈无渊",
        "genre": "暗黑玄幻",
        "target_platform": "抖音短剧/番茄小说",
        "chapter_length": "2500-3000字",
        "style": "3D国漫电影叙事节奏，画面感极强",
        "characters": {
            "沈无渊": {
                "role": "主角",
                "age": "20岁",
                "appearance": "清瘦结实一米七八，面容冷峻，左眼角浅疤，黑瞳深邃，黑色长发凌乱，褪色青衫",
                "personality": "外冷内隐忍，惜字如金，极度理性，为达目的不择手段但不伤无辜",
                "taboo": "宗门往事，父母下落",
                "weapon": "短刀",
            },
            "秦墨": {
                "role": "对手/执法堂首席",
                "age": "26岁",
                "appearance": "银白执法袍，面无表情，目光锐利如刀",
                "personality": "冷酷正统，代表宗门秩序",
            },
            "苏晚晴": {
                "role": "女主/神秘",
                "age": "22岁",
                "appearance": "银发紫瞳，半张狐狸面具",
                "personality": "亦正亦邪，情报贩子/算命师，善恶不明",
            },
            "陆九幽": {
                "role": "兄弟/蛊师",
                "age": "25岁",
                "appearance": "病态苍白，黑袍金蛊纹",
                "personality": "疯癫不羁，嗜蛊如命",
            },
            "月姬": {
                "role": "反派BOSS",
                "age": "容貌25岁(实际百岁)",
                "appearance": "白色宫装，月白长发，银色眼瞳",
                "personality": "温柔外表下极端危险",
            },
            "玄机老人": {
                "role": "导师",
                "age": "70岁",
                "appearance": "灰色粗布道袍，竹杖七道裂痕，白须",
                "personality": "看似糊涂实则深不可测",
            },
        },
        "golden_finger": {
            "name": "噬魂蛊",
            "ability": "吞噬他人精血后获得部分记忆和修为",
            "limit_1": "每次吞噬后24小时虚弱",
            "limit_2": "7天不吞噬则反噬宿主",
            "growth": "吞噬越强对手获得越强能力，但风险越大(可能被反噬意识)",
            "side_effect": "被吞噬者的情绪/记忆短暂涌入，造成精神痛苦",
        },
        "world_setting": "天衍宗为正道大宗，表面光正，内部腐败。宗主暗中修炼禁术。",
        "arc_outline": [
            "第1章: 除名 — 沈无渊被天衍宗当众除名，地牢发现噬魂蛊，吞噬后反杀灭口杀手",
            "第2章: 逃亡 — 沈无渊逃出天衍宗，途中遭遇追杀，首次主动使用噬魂蛊",
            "第3章: 苏晚晴 — 逃入荒镇，遇到神秘的苏晚晴，得到第一条线索",
            "第4章: 暗市 — 进入地下暗市，发现天衍宗的黑暗交易",
            "第5章: 陆九幽 — 遇到疯癫蛊师陆九幽，被迫合作",
            "第6章: 反噬 — 七天期限到，噬魂蛊反噬，濒死关头突破",
            "第7章: 玄机老人 — 遇到隐世高人玄机老人，得知噬魂蛊的真正来历",
            "第8章: 秘密 — 发现父母失踪与天衍宗宗主有关",
            "第9章: 复仇开始 — 潜回天衍宗外围，第一次正面对抗秦墨",
            "第10章: 月姬登场 — 幕后BOSS月姬首次现身，力量碾压",
        ],
    }
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info(f"Created default config: {CONFIG_PATH}")
    return cfg


def load_progress() -> dict:
    """加载进度"""
    if PROGRESS_PATH.exists():
        return json.loads(PROGRESS_PATH.read_text(encoding="utf-8"))
    return {"last_chapter": 0, "chapters_written": [], "total_tokens_used": 0}


def save_progress(progress):
    """原子写入进度（临时文件 → rename）"""
    tmp = PROGRESS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.rename(PROGRESS_PATH)


def build_system_prompt(cfg):
    """构建 system prompt"""
    chars_desc = ""
    for name, info in cfg["characters"].items():
        chars_desc += f"\n**{name}** ({info['role']}): {info['age']}，{info['appearance']}。{info['personality']}。"

    gf = cfg["golden_finger"]

    return (
        f"你是一位顶尖的中国网络文学作者，擅长写暗黑玄幻短剧剧本。\n"
        f"你正在写一部叫《{cfg['novel_name']}》的连载小说，目标平台: {cfg['target_platform']}。\n"
        f"你的作品将被改编成3D国漫短剧，每一段文字都必须有极强的画面感。\n\n"
        f"## 角色设定{chars_desc}\n\n"
        f"## 金手指: {gf['name']}\n"
        f"- 能力: {gf['ability']}\n"
        f"- 限制1: {gf['limit_1']}\n"
        f"- 限制2: {gf['limit_2']}\n"
        f"- 成长: {gf['growth']}\n"
        f"- 副作用: {gf['side_effect']}\n\n"
        f"## 世界观\n{cfg['world_setting']}\n\n"
        f"## 写作规则（爆款框架）\n"
        f"1. 每章 = 钩子+冲突+爽点+悬念结尾\n"
        f"2. 爽点密度: 铺垫:爽点:反转 = 3:4:3，一章至少2个'卧槽'时刻\n"
        f"3. 展示而非陈述: '他愤怒'→'他的手在抖'\n"
        f"4. 禁止: 连续背景介绍超3句; '总之/综上/没想到竟然/不由得'; 连续3个感叹号\n"
        f"5. 每段有画面感，用'---'分隔分镜段落（每个---之间是一个独立镜头）\n"
        f"6. 对白短促有力，每句不超15字\n"
        f"7. {cfg['chapter_length']}，直接输出正文\n"
        f"8. 动作场面具体到肢体动作、光影变化\n"
        f"9. 每章结尾留悬念钩子\n"
        f"10. 保持角色人设一致，沈无渊永远冷峻惜字如金"
    )


def build_chapter_prompt(cfg, chapter_num, prev_summary, arc_hint):
    """构建单章写作 prompt"""
    parts = [f"写《{cfg['novel_name']}》第{chapter_num}章。\n"]

    if arc_hint:
        parts.append(f"本章大纲提示: {arc_hint}\n")

    if prev_summary:
        parts.append(f"前情摘要（保持连贯）:\n{prev_summary}\n")

    parts.append(
        "要求:\n"
        "- 直接输出正文，不要标题/前言/说明\n"
        "- 用---分隔可独立成分镜的段落\n"
        "- 确保与前文剧情连贯\n"
        "- 本章必须有至少2个高潮/爽点\n"
        "- 结尾留悬念"
    )
    return "\n".join(parts)


def build_summary_prompt(chapter_text, chapter_num):
    """让 AI 总结本章，作为下章的前情"""
    cfg = load_config()
    novel_name = cfg.get("novel_name", "禁蛊录")
    return (
        f"以下是《{novel_name}》第{chapter_num}章的正文。\n"
        f"请用100-150字总结本章的关键情节、角色状态变化、悬念，供下一章续写参考。\n"
        f"只输出摘要，不要其他文字。\n\n"
        f"---\n{chapter_text[:3000]}\n---"
    )


def call_deepseek(system, user, model="deepseek-reasoner", max_tokens=4096, temperature=0.85):
    """调用 DeepSeek API"""
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "top_p": 0.9,
    }
    for attempt in range(3):
        try:
            r = requests.post(API_URL, headers=headers, json=payload, timeout=180)
            r.raise_for_status()
            data = r.json()
            text = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {})
            return text, usage
        except Exception as e:
            log.warning(f"  API call attempt {attempt+1} failed: {e}")
            if attempt < 2:
                time.sleep(5 * (2 ** attempt))
    return None, {}


def generate_chapter(cfg, chapter_num, prev_summary, system_prompt):
    """生成单章"""
    # 获取大纲提示
    arc_hint = ""
    if chapter_num <= len(cfg.get("arc_outline", [])):
        arc_hint = cfg["arc_outline"][chapter_num - 1]

    user_prompt = build_chapter_prompt(cfg, chapter_num, prev_summary, arc_hint)

    log.info(f"📝 正在生成第{chapter_num}章...")
    t = time.time()
    text, usage = call_deepseek(system_prompt, user_prompt)
    elapsed = time.time() - t

    if not text:
        log.error(f"❌ 第{chapter_num}章生成失败")
        return None, None, {}

    log.info(f"✅ 第{chapter_num}章完成: {len(text)}字, {elapsed:.1f}s, tokens={usage.get('total_tokens', 0)}")

    # 生成摘要
    summary_prompt = build_summary_prompt(text, chapter_num)
    summary, _ = call_deepseek(
        "你是一个小说摘要助手，只输出简洁的情节摘要。",
        summary_prompt,
        model="deepseek-chat",  # 摘要用快速模式
        max_tokens=300,
        temperature=0.3,
    )

    return text, summary, usage


def run_ai_novel_generator(seed_path: str, start_chapter: int = 1, only: str = None) -> bool:
    """
    调用 AI_NovelGenerator/generate_novel.py 生成小说章节，然后转换为 queue 格式。
    seed_path: YAML 种子文件路径 (AI_NovelGenerator/seeds/ 目录下)
    Returns: True if 至少一章生成成功
    """
    engine_script = AI_NOVEL_ENGINE / "generate_novel.py"
    if not engine_script.exists():
        log.error(f"❌ AI_NovelGenerator 脚本不存在: {engine_script}")
        return False

    cmd = [sys.executable, str(engine_script), "--seed", seed_path]
    if start_chapter > 1:
        cmd += ["--start-chapter", str(start_chapter)]
    if only:
        cmd += ["--only", only]

    log.info(f"📚 启动 AI_NovelGenerator: seed={seed_path} start={start_chapter}")
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(AI_NOVEL_ENGINE),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            encoding="utf-8",
            errors="replace",
        )
        for line in proc.stdout:
            log.info(f"  [Engine] {line.rstrip()}")
        proc.wait()
        log.info(f"  Engine exit code: {proc.returncode}")
    except Exception as e:
        log.error(f"❌ AI_NovelGenerator 运行失败: {e}")
        return False

    # Engine 输出到 AI_NovelGenerator/novels/{novel_name}/chapters/chapter_N.txt
    # 识别 seed 中的 novel_name
    try:
        with open(seed_path, "r", encoding="utf-8") as f:
            seed = yaml.safe_load(f)
    except Exception as e:
        log.error(f"❌ 读取 seed 失败: {e}")
        return False

    novel_name = seed.get("novel_name", "")
    chapters_dir = AI_NOVEL_ENGINE / "novels" / novel_name / "chapters"
    if not chapters_dir.exists():
        log.warning(f"⚠️ 章节目录不存在: {chapters_dir}")
        return False

    # 将 chapter_N.txt 转换为 queue 格式
    converted = 0
    for txt_file in sorted(chapters_dir.glob("chapter_*.txt")):
        try:
            num = int(txt_file.stem.split("_")[1])
        except (IndexError, ValueError):
            continue
        queue_file = QUEUE_DIR / f"chapter_{num:03d}.json"
        if queue_file.exists():
            log.info(f"  ⏭️ 第{num}章 queue 已存在，跳过")
            continue
        text = txt_file.read_text(encoding="utf-8").strip()
        if not text:
            log.warning(f"  ⚠️ 第{num}章文件内容为空，跳过")
            continue
        segments = [s.strip() for s in text.split("---") if s.strip()]
        queue_data = {
            "chapter_num": num,
            "text": text,
            "summary": "",
            "segments": segments,
            "segment_count": len(segments),
            "char_count": len(text),
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "status": "ready",
            "source": "AI_NovelGenerator",
        }
        queue_file.write_text(json.dumps(queue_data, ensure_ascii=False, indent=2), encoding="utf-8")
        out_file = _get_output_dir(novel_name) / f"chapter_{num:03d}.txt"
        out_file.write_text(text, encoding="utf-8")
        log.info(f"  ✅ 第{num}章 转入 queue ({len(segments)} 分镜段)")
        converted += 1

    log.info(f"📚 AI_NovelGenerator 完成: 新增 {converted} 章入队列")
    return converted > 0


def save_chapter(chapter_num, text, summary, novel_name="禁蛊录"):
    """保存章节到 output/{novel_name}/ 和 queue"""
    # Output (永久存储)
    out_file = _get_output_dir(novel_name) / f"chapter_{chapter_num:03d}.txt"
    out_file.write_text(text, encoding="utf-8")

    # Queue (供下游分镜工厂消费)
    queue_file = QUEUE_DIR / f"chapter_{chapter_num:03d}.json"
    if queue_file.exists():
        log.warning(f"  ⚠️ queue 文件已存在，跳过覆盖: {queue_file.name}")
        return
    queue_data = {
        "chapter_num": chapter_num,
        "text": text,
        "summary": summary or "",
        "segments": [s.strip() for s in text.split("---") if s.strip()],
        "segment_count": len([s for s in text.split("---") if s.strip()]),
        "char_count": len(text),
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "status": "ready",
    }
    queue_file.write_text(json.dumps(queue_data, ensure_ascii=False, indent=2), encoding="utf-8")

    log.info(f"  💾 Saved: {out_file.name} + queue/{queue_file.name} ({len(queue_data['segments'])} segments)")


def main():
    parser = argparse.ArgumentParser(description="📖 小说工厂")
    parser.add_argument("--chapters", type=int, default=10, help="生成章节数 (默认10)")
    parser.add_argument("--start", type=int, default=0, help="起始章节号 (0=自动从上次续写)")
    parser.add_argument("--daemon", action="store_true", help="守护模式: 持续运行，每小时检查并补充")
    parser.add_argument("--min-queue", type=int, default=5, help="守护模式: 队列低于此数自动补充")
    parser.add_argument("--engine", action="store_true", help="使用 AI_NovelGenerator 引擎 (更强, 需要 --seed)")
    parser.add_argument("--seed", default="", help="AI_NovelGenerator 种子文件 (YAML, 相对 AI_NovelGenerator 目录)")
    parser.add_argument("--only", choices=["architecture", "blueprint", "draft"], default=None, help="仅执行某一步 (engine 模式)")
    args = parser.parse_args()

    # — AI_NovelGenerator 引擎模式 —
    if args.engine:
        if not args.seed:
            log.error("❌ --engine 模式必须指定 --seed 文件")
            sys.exit(1)
        seed_path = args.seed if os.path.isabs(args.seed) else str(AI_NOVEL_ENGINE / args.seed)
        start_ch = args.start if args.start > 0 else 1
        ok = run_ai_novel_generator(seed_path, start_chapter=start_ch, only=args.only)
        sys.exit(0 if ok else 1)

    cfg = load_config()
    novel_name = cfg.get("novel_name", "禁蛊录")
    progress = load_progress()
    system_prompt = build_system_prompt(cfg)

    start_chapter = args.start if args.start > 0 else progress["last_chapter"] + 1
    end_chapter = start_chapter + args.chapters - 1

    log.info(f"{'='*50}")
    log.info(f"📖 {novel_name} 小说工厂启动")
    log.info(f"  范围: 第{start_chapter}章 → 第{end_chapter}章")
    log.info(f"  模型: DeepSeek V4-Flash (reasoner)")
    log.info(f"{'='*50}")

    prev_summary = ""
    # 尝试加载上一章的摘要
    if start_chapter > 1:
        prev_queue = QUEUE_DIR / f"chapter_{start_chapter-1:03d}.json"
        if prev_queue.exists():
            prev_data = json.loads(prev_queue.read_text(encoding="utf-8"))
            prev_summary = prev_data.get("summary", "")
            log.info(f"  📎 加载前章摘要: {prev_summary[:50]}...")

    for ch in range(start_chapter, end_chapter + 1):
        # 检查是否已存在
        if (_get_output_dir(novel_name) / f"chapter_{ch:03d}.txt").exists():
            log.info(f"⏭️ 第{ch}章已存在，跳过")
            # 加载其摘要供下章使用
            qf = QUEUE_DIR / f"chapter_{ch:03d}.json"
            if qf.exists():
                prev_summary = json.loads(qf.read_text(encoding="utf-8")).get("summary", "")
            continue

        text, summary, usage = generate_chapter(cfg, ch, prev_summary, system_prompt)
        if not text:
            log.error(f"第{ch}章失败，停止")
            break

        save_chapter(ch, text, summary)

        # 更新进度
        prev_summary = summary or ""
        progress["last_chapter"] = ch
        progress["chapters_written"].append(ch)
        progress["total_tokens_used"] += usage.get("total_tokens", 0)
        save_progress(progress)

        # 间隔2秒避免API限流
        time.sleep(2)

    log.info(f"\n{'='*50}")
    log.info(f"📖 小说工厂完成")
    log.info(f"  已生成: {len(progress['chapters_written'])}章")
    log.info(f"  总tokens: {progress['total_tokens_used']}")
    log.info(f"  输出: {OUTPUT_BASE / novel_name}")
    log.info(f"  队列: {QUEUE_DIR}")
    log.info(f"{'='*50}")

    # 守护模式
    if args.daemon:
        log.info("🔄 进入守护模式...")
        shutdown_flag = False

        def _handle_signal(sig, frame):
            nonlocal shutdown_flag
            log.info(f"🛑 收到信号 {sig}，正在优雅退出...")
            shutdown_flag = True

        signal.signal(signal.SIGINT, _handle_signal)
        signal.signal(signal.SIGTERM, _handle_signal)

        while not shutdown_flag:
            ready = len(list(QUEUE_DIR.glob("chapter_*.json")))
            if ready < args.min_queue:
                log.info(f"  队列仅{ready}章，补充到{args.min_queue + 5}...")
                next_ch = progress["last_chapter"] + 1
                for i in range(args.min_queue + 5 - ready):
                    if shutdown_flag:
                        break
                    ch = next_ch + i
                    text, summary, usage = generate_chapter(cfg, ch, prev_summary, system_prompt)
                    if text:
                        save_chapter(ch, text, summary)
                        prev_summary = summary or ""
                        progress["last_chapter"] = ch
                        progress["chapters_written"].append(ch)
                        progress["total_tokens_used"] += usage.get("total_tokens", 0)
                        save_progress(progress)
                        time.sleep(2)
            else:
                log.info(f"  队列{ready}章，充足，休息60s...")
            # 用短 sleep 分片替代长 sleep，以便及时响应信号
            for _ in range(60):
                if shutdown_flag:
                    break
                time.sleep(1)
        log.info("👋 守护模式已退出")


if __name__ == "__main__":
    main()

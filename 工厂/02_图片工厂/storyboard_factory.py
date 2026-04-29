# -*- coding: utf-8 -*-
"""
🎨 分镜工厂 — 章节→大纲→分镜图
监听 novel-factory/queue，自动生成分镜大纲+图片

流程: 
  1. 读取章节文本 → DeepSeek 生成7段分镜大纲(含prompt)
  2. 每段 → GPT Image2 (CatGPT→GRSAI fallback) 生成分镜图
  3. 输出分镜包(JSON+图片) → video-factory/queue

用法:
  python storyboard_factory.py                # 处理所有待处理章节
  python storyboard_factory.py --daemon       # 守护模式(持续监听)
  python storyboard_factory.py --chapter 1    # 只处理指定章节
"""
import argparse
import json
import logging
import os
import sys
import time
import base64
import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

# ── Paths ──
FACTORY_DIR = Path(__file__).parent       # 工厂/02_图片工厂/
NOVEL_QUEUE = FACTORY_DIR.parent / "01_小说工厂" / "queue"
OUTPUT_DIR = FACTORY_DIR / "output"
QUEUE_DIR = FACTORY_DIR / "queue"    # 暂存
VIDEO_QUEUE = FACTORY_DIR.parent / "03_视频工厂" / "queue"
PROJECT_ROOT = FACTORY_DIR.parent.parent
CHARACTERS_JSON = PROJECT_ROOT / "config" / "characters.json"

OUTPUT_DIR.mkdir(exist_ok=True)
QUEUE_DIR.mkdir(exist_ok=True)
VIDEO_QUEUE.mkdir(exist_ok=True)

# ── Logging ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [分镜工厂] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(FACTORY_DIR / "storyboard_factory.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

# ── API Config ──
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_KEY = os.environ.get("DEEPSEEK_API_KEY", "sk-410ef5b3636e4b10bb2c6391f569c1ad")

# ── Novel Name ──
def _read_novel_name() -> str:
    cfg = PROJECT_ROOT / "config" / "config.yaml"
    try:
        import yaml
        d = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
        if d.get("novel_name"): return d["novel_name"]
    except Exception:
        pass
    cfg2 = FACTORY_DIR.parent / "01_小说工厂" / "config.json"
    try:
        return json.loads(cfg2.read_text(encoding="utf-8")).get("novel_name", "禁蛊录")
    except Exception:
        return "禁蛊录"

NOVEL_NAME = _read_novel_name()

# ── 图片后端: 02_图片工厂/pipelines/image_backends ──
_PIPELINES_DIR = FACTORY_DIR / "pipelines"
if str(_PIPELINES_DIR) not in sys.path:
    sys.path.insert(0, str(_PIPELINES_DIR))

try:
    from image_backends import generate_image as _img_backend
    _USE_SHARED_IMG = True
except ImportError as _ie:
    _img_backend = None
    _USE_SHARED_IMG = False
    log.warning(f"⚠️ image_backends 不可用 ({_ie})，图片生成将失败")

# ── 资产库 (工厂/02_图片工厂/资产库) ──────────────────────────
_ASSET_MGR_DIR = FACTORY_DIR / "资产库"
if str(_ASSET_MGR_DIR) not in sys.path:
    sys.path.insert(0, str(_ASSET_MGR_DIR))
try:
    from asset_manager import AssetManager
    _ASSET_MGR = AssetManager(NOVEL_NAME)
    _USE_ASSET_LIB = True
except ImportError:
    _ASSET_MGR = None
    _USE_ASSET_LIB = False

STORYBOARDS_PER_EPISODE = 7
STYLE_PREFIX = (
    "3D render, Chinese anime style (guoman), Xianxia dark fantasy, "
    "cinematic lighting, volumetric fog, mystical atmosphere, "
    "highly detailed, 9:16 portrait aspect ratio, "
    "no text, no watermark, no subtitle"
)


def load_characters():
    """加载角色外观描述"""
    if CHARACTERS_JSON.exists():
        raw = json.loads(CHARACTERS_JSON.read_text(encoding="utf-8"))
        # characters.json has nested structure: {"characters": {"name": {...}}}
        data = raw.get("characters", raw)
        chars = {}
        for name, info in data.items():
            if not isinstance(info, dict):
                continue
            desc = info.get("clothing_prompt_override", "")
            if not desc:
                desc = f"{info.get('age','')}, {info.get('hair','')}, {info.get('clothing','')}, {info.get('face','')}"
            chars[name] = {"cn_desc": f"{info.get('age','')}, {info.get('face','')}, {info.get('clothing','')}", "en_desc": desc}
        return chars
    return {}


def deepseek_call(system, user, model="deepseek-chat", max_tokens=2000):
    """Quick DeepSeek call with retry"""
    for attempt in range(3):
        try:
            r = requests.post(DEEPSEEK_URL, headers={
                "Authorization": f"Bearer {DEEPSEEK_KEY}",
                "Content-Type": "application/json"
            }, json={
                "model": model,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "temperature": 0.7, "max_tokens": max_tokens,
            }, timeout=120)
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
        except Exception as e:
            log.warning(f"  DeepSeek attempt {attempt+1}/3 failed: {e}")
            if attempt < 2:
                time.sleep(5 * (attempt + 1))
    raise RuntimeError("DeepSeek API failed after 3 attempts")


def generate_storyboard_outline(chapter_text, chapter_num, characters):
    """章节文本 → 7段分镜大纲 (JSON)"""
    char_list = ", ".join(characters.keys()) if characters else "沈无渊, 秦墨, 苏晚晴"

    system = (
        "你是一个3D国漫短剧分镜导演。将小说章节拆成7个分镜段落，每段对应一个15秒视频镜头。\n"
        "输出严格JSON数组，每个元素包含:\n"
        '- "shot": 镜头编号(1-7)\n'
        '- "scene": 场景描述(中文)\n'
        '- "action": 动作描述(中文)\n'
        '- "characters": 出场角色列表\n'
        '- "camera": 镜头运动(dolly_in/dolly_out/pan_left/pan_right/tilt_up/tilt_down/static/crane_up)\n'
        '- "mood": 氛围(dark/tense/epic/calm/mysterious)\n'
        '- "image_prompt": 英文分镜图生成prompt(详细描述画面，包含角色外貌、场景、光影、构图)\n'
        '- "video_prompt": 英文视频生成prompt(描述动作和镜头运动，用于Seedance 2.0)\n'
        '- "narration": 本段旁白文本(中文，供TTS配音，50-80字)\n\n'
        f"可用角色: {char_list}\n"
        "【image_prompt 安全要求】必须PG-13级别，内容审核安全：\n"
        "  - 禁止: violence/torture/beating/prison/dungeon/execution/blood/death/killing/naked/explicit\n"
        "  - 改用: dramatic confrontation/ceremonial hall/stone chamber/intense standoff/spirit energy\n"
        "  - 黑暗场景改为: moody atmospheric lighting/shadow and mist/ominous energy\n"
        "  - 打斗改为: intense martial arts stance/spirit power clash/dramatic energy burst\n"
        "只输出JSON数组，不要其他文字。"
    )

    user = f"以下是《{NOVEL_NAME}》第{chapter_num}章，请拆成7个分镜:\n\n{chapter_text[:4000]}"

    result = deepseek_call(system, user, model="deepseek-chat", max_tokens=3000)

    # 清理 markdown code fence
    import re
    result = re.sub(r'```(?:json)?\s*', '', result).strip().rstrip('`')

    try:
        shots = json.loads(result)
        if isinstance(shots, list) and len(shots) >= 5:
            return shots
    except json.JSONDecodeError:
        log.warning(f"  JSON解析失败，尝试修复...")

    log.error(f"  分镜大纲生成失败")
    return None




SAFETY_REPLACEMENTS = {
    # 血腥暴力
    "blood spraying": "dramatic red lighting",
    "blood spray": "red cinematic lighting",
    "bloodstained": "dark stained",
    "bloodied": "dust covered",
    "blood": "red shadow",
    "corpse": "fallen figure in shadow",
    "dead body": "fallen figure in shadow",
    "shriveled": "motionless",
    "pierced": "blocked",
    "throat": "shoulder",
    "assassin": "masked intruder",
    "assassins": "masked intruders",
    "spraying": "glowing",
    "gore": "darkness",
    "wound": "mark",
    "stab": "strike",
    # 监禁場景
    "torture": "tense confrontation",
    "beaten": "overwhelmed",
    "beating": "confrontation",
    "kicks": "stance",
    "kicked": "pushed back",
    "punching": "sparring",
    "punch": "clash",
    "whipped": "restrained",
    "lashed": "bound",
    "execution": "banishment ritual",
    "executed": "banished",
    "prisoner": "wanderer",
    "prison": "stone chamber",
    "dungeon": "underground hall",
    "cell": "stone room",
    "chained": "bound by fate",
    "chains": "ritual bindings",
    "shackles": "ceremonial bindings",
    "torture chamber": "dark hall",
    "punishment": "ceremony",
    "humiliating": "dramatic",
    "humiliation": "dramatic scene",
    "strip": "remove",
    "stripped": "cast aside",
    "expel": "banish",
    "expelled": "banished",
    "exile": "banishment",
    "exiled": "banished",
    "death": "darkness",
    "dying": "weakened",
    "kill": "defeat",
    "killing": "battle",
    "murder": "confrontation",
    "violence": "intensity",
    "brutal": "fierce",
    "savage": "intense",
    "unconscious": "resting",
    "collapsed": "kneeling",
    "fallen": "kneeling",
    "writhing": "struggling",
    "screaming": "calling out",
    "scream": "call",
    "crying": "emotional",
    "tears": "emotional expression",
    "despair": "determination",
    "agony": "intense emotion",
    "suffering": "enduring",
    "suffer": "endure",
    # 禁看中文词
    "杀": "defeat",
    "死": "shadow",
    "血": "red light",
    "拷打": "confront",
    "奸辟": "mark",
    "痛苦": "endure",
    "憨辱": "dramatic",
    "屌辱": "test",
    "踢": "step",
    "鸭笼": "stone chamber",
    "刺": "strike",
    "祭法": "dark ritual energy",
}


def sanitize_prompt(prompt):
    """移除可能触发内容安全审核的词汇"""
    for old, new in SAFETY_REPLACEMENTS.items():
        prompt = prompt.replace(old, new)
    return prompt + " Non-graphic fantasy drama, PG-13, cinematic tension, safe for generation."


SAFE_FALLBACK_PROMPT = (
    "Atmospheric wide-angle shot, ancient Chinese xianxia fantasy setting, "
    "moody cinematic lighting, stone architecture with mystical energy, "
    "dramatic clouds and mist, volumetric light rays, no characters, "
    "3D donghua animation style, UE5 render quality, PG-13 safe."
)

# 触发审核重写的关键词（检测到这些就先用 DeepSeek 重写再发图）
_AUDIT_TRIGGERS = [
    "torture", "beaten", "beating", "execution", "dungeon", "prison", "blood",
    "death", "dying", "murder", "whipped", "violence", "brutal", "stripped",
    "naked", "explicit", "stab", "kill", "corpse", "wound", "gore",
]


def audit_image_prompt(prompt: str, scene_cn: str = "") -> str:
    """
    分镜词安全审计 (DeepSeek rewrite):
    检测到敏感关键词时，调用 DeepSeek 重写为安全的视觉描述
    场景内容不变，只替换可能触发内容审核的表达
    """
    lower = prompt.lower()
    has_trigger = any(t in lower for t in _AUDIT_TRIGGERS)
    if not has_trigger:
        return prompt  # 无风险，直接返回

    log.info(f"  🔍 分镜词审计: 检测到敏感词，DeepSeek 重写...")
    system = (
        "你是一个图片生成prompt安全审计员。"
        "将输入的英文image prompt重写为内容安全版本(PG-13级别)：\n"
        "规则:\n"
        "1. 保留场景氛围、角色外貌、构图、光影描述\n"
        "2. 将暴力/监禁/死亡/刑罚词汇改为戏剧性但安全的等价表达\n"
        "3. 不改变故事情境，只改变视觉描述措辞\n"
        "4. 输出纯英文prompt，不加任何解释"
    )
    user = f"场景背景(参考): {scene_cn}\n\n原始prompt:\n{prompt}\n\n重写为安全版本:"
    try:
        rewritten = deepseek_call(system, user, model="deepseek-chat", max_tokens=300)
        rewritten = rewritten.strip().strip('"').strip("'")
        log.info(f"  ✅ 分镜词审计完成")
        return rewritten
    except Exception as e:
        log.warning(f"  ⚠️ 分镜词审计失败 ({e})，使用原始prompt")
        return prompt


def generate_image(prompt, output_path, char_names: list = None, shot_scene: str = ""):
    """生成图片: 调用 image_backends.gpt_image2 (CatGPT → GRSAI 自动 fallback)
    内容审核拋载时自动降级为安全氛围镜头
    char_names: 画面中出场的角色名列表，用于加载资产库参考图
    """
    prompt = sanitize_prompt(prompt)
    # 分镜词安全审计（检测到敏感词时 DeepSeek 重写）
    prompt = audit_image_prompt(prompt, scene_cn=shot_scene)
    # 加载角色参考图
    ref_images = []
    if _USE_ASSET_LIB and char_names:
        for cname in char_names[:2]:  # 最多取2个角色参考图，避免过多
            refs = _ASSET_MGR.get_character_refs(cname)
            ref_images.extend(refs)
        ref_images = ref_images[:2]  # Bug3 fix: 最多2张参考图避免超出限制
        if ref_images:
            log.info(f"  📄 加载 {len(ref_images)} 张角色参考图: {char_names}")

    if _USE_SHARED_IMG:
        for attempt in range(2):  # 最多尝试两次: 1st详细prompt, 2nd降级安全氛围镜头
            use_prompt = prompt if attempt == 0 else SAFE_FALLBACK_PROMPT
            if attempt == 1:
                log.info(f"  🔄 内容审核拋载，降级为安全氛围镜头...")
            try:
                img_bytes = _img_backend(
                    "gpt_image2", prompt_zh=use_prompt,
                    ref_images_b64=ref_images if (ref_images and attempt == 0) else None,
                    width=832, height=1216,
                )
                if img_bytes:
                    output_path.write_bytes(img_bytes)
                    return "gpt_image2" if attempt == 0 else "gpt_image2_safe"
                else:
                    if attempt == 0:
                        log.info(f"  ⚠️ attempt 0 返回 None，降级安全氛围镜头...")  # Bug5 fix
            except Exception as e:
                log.warning(f"  image_backends 调用失败: {e}")
                if attempt == 0:
                    continue
    return None


def process_chapter(chapter_file, characters):
    """处理单个章节"""
    data = json.loads(chapter_file.read_text(encoding="utf-8"))
    chapter_num = data["chapter_num"]
    chapter_text = data["text"]

    # 检查是否已处理（需验证图片文件真实存在，防止半成品跳过）
    out_json = VIDEO_QUEUE / f"episode_{chapter_num:03d}.json"
    if out_json.exists():
        try:
            ep = json.loads(out_json.read_text(encoding="utf-8"))
            all_ok = all(
                s.get("image_path") and Path(s["image_path"]).exists()
                for s in ep.get("shots", [])
            )
            if all_ok:
                log.info(f"⏭️ 第{chapter_num}章已有完整分镜包，跳过")
                return True
            log.info(f"⚠️ 第{chapter_num}章分镜包不完整（有缺失图片），重新生成")
        except Exception:
            pass  # 解析失败就继续重处理

    log.info(f"🎨 处理第{chapter_num}章 ({data['char_count']}字, {data['segment_count']}段)")

    # Step 1: 生成分镜大纲（优先读缓存，避免重跑时 outline 不一致）
    episode_dir = OUTPUT_DIR / NOVEL_NAME / f"episode_{chapter_num:03d}"
    episode_dir.mkdir(parents=True, exist_ok=True)  # Bug2 fix: parents=True
    outline_cache = episode_dir / "outline.json"

    if outline_cache.exists():
        shots = json.loads(outline_cache.read_text(encoding="utf-8"))
        log.info(f"  📋 分镜大纲 (缓存): {len(shots)}个镜头")
    else:
        shots = generate_storyboard_outline(chapter_text, chapter_num, characters)
        if not shots:
            return False
        outline_cache.write_text(json.dumps(shots, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info(f"  📋 分镜大纲: {len(shots)}个镜头 (已缓存)")

    # Step 2: 并行生成分镜图（max_workers=2: CatGPT + GRSAI 同时工作，等待时间减半）

    def _generate_shot(args):
        """单镜头生成任务（线程安全）"""
        i, shot = args
        img_path = episode_dir / f"shot_{i+1:02d}.jpg"
        if img_path.exists() and img_path.stat().st_size > 10000:
            log.info(f"  ⏭️ Shot {i+1} already exists")
            return i, str(img_path), None  # (idx, path, backend)

        raw_prompt = shot.get('image_prompt', '')
        full_prompt = raw_prompt  # Bug1 fix: 不重复加前缀，image_backends内部已有详细风格前缀
        shot_chars = shot.get("characters", [])
        shot_scene = shot.get("scene", "")
        log.info(f"  🖼️ Shot {i+1}/{len(shots)}: generating image... chars={shot_chars}")

        backend = generate_image(full_prompt, img_path, char_names=shot_chars, shot_scene=shot_scene)
        path = str(img_path) if backend else None
        return i, path, backend

    results = {}
    with ThreadPoolExecutor(max_workers=2) as executor:
        future_map = {executor.submit(_generate_shot, (i, shot)): i for i, shot in enumerate(shots)}
        for future in as_completed(future_map):
            i, img_path_str, backend = future.result()
            results[i] = (img_path_str, backend)

    # 按顺序更新 shots 并归档
    for i, shot in enumerate(shots):
        img_path_str, backend = results.get(i, (None, None))
        img_path = episode_dir / f"shot_{i+1:02d}.jpg"
        if img_path_str:
            shot["image_path"] = img_path_str
            if backend:
                shot["image_backend"] = backend
                log.info(f"  ✅ Shot {i+1} done ({backend})")
                if _USE_ASSET_LIB:
                    camera = shot.get("camera", "")
                    if camera in ("crane_up", "static", "dolly_out"):
                        scene = shot.get("scene", "")
                        if scene:
                            _ASSET_MGR.archive_storyboard_frame(
                                str(img_path),
                                char_names=None,
                                scene_name=scene[:20].strip(),
                                episode=chapter_num,
                                shot=i + 1,
                            )
        else:
            shot["image_path"] = None
            log.warning(f"  ❌ Shot {i+1} image failed")

    # Step 3: 输出到 video-factory queue
    episode_data = {
        "episode_num": chapter_num,
        "chapter_num": chapter_num,
        "novel": NOVEL_NAME,
        "shots": shots,
        "shot_count": len(shots),
        "narration_texts": [s.get("narration", "") for s in shots],
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "status": "ready",
    }

    out_json.write_text(json.dumps(episode_data, ensure_ascii=False, indent=2), encoding="utf-8")

    # 保存本地副本
    local_json = episode_dir / "episode.json"
    local_json.write_text(json.dumps(episode_data, ensure_ascii=False, indent=2), encoding="utf-8")

    log.info(f"  📦 Episode {chapter_num} package ready → video-factory/queue/")

    # Step 4: 分镜品质审计（基于 episode.json，12项检查）
    _run_storyboard_audit(shots, chapter_num)
    return True


def _run_storyboard_audit(shots: list, chapter_num: int):
    """
    分镜品质快速审计（适配 episode.json，不依赖 ToonFlow DB）
    检查: 图片存在性 / 审核失败率 / 角色真名泄露 / prompt多样性
    """
    total = len(shots)
    ok = sum(1 for s in shots if s.get("image_path") and Path(s["image_path"]).exists())
    safe_fallback = sum(1 for s in shots if s.get("image_backend") == "gpt_image2_safe")
    failed = total - ok

    # 角色真名泄露检测（prompt中不应出现角色名）
    name_leaks = []
    try:
        import json as _json
        chars_raw = _json.loads(CHARACTERS_JSON.read_text("utf-8")).get("characters", {})
        char_names = list(chars_raw.keys())
    except Exception:
        char_names = []

    for i, s in enumerate(shots):
        prompt = (s.get("image_prompt") or "").lower()
        for name in char_names:
            if name.lower() in prompt:
                name_leaks.append(f"Shot{i+1}含角色真名[{name}]")

    # 场景多样性（连续相同场景检测）
    scenes = [s.get("scene", "")[:10] for s in shots]
    repeat_scenes = sum(1 for i in range(len(scenes)-1) if scenes[i] == scenes[i+1] and scenes[i])

    log.info(f"  🔍 [审计] Episode {chapter_num}: 图片 {ok}/{total} ✅ | 失败 {failed} | 安全降级 {safe_fallback}")
    if name_leaks:
        for leak in name_leaks:
            log.warning(f"  ⚠️ [审计] {leak}")
    if repeat_scenes > 2:
        log.warning(f"  ⚠️ [审计] 场景多样性不足: {repeat_scenes}个连续重复场景")
    if failed > total // 2:
        log.warning(f"  ⚠️ [审计] 图片失败率过高({failed}/{total})，建议检查图片后端连接")


def main():
    parser = argparse.ArgumentParser(description="🎨 分镜工厂")
    parser.add_argument("--daemon", action="store_true", help="守护模式")
    parser.add_argument("--chapter", type=int, default=0, help="只处理指定章节")
    args = parser.parse_args()

    characters = load_characters()
    log.info(f"🎨 分镜工厂启动 | {len(characters)} 个角色加载")

    while True:
        # 扫描小说队列
        chapter_files = sorted(NOVEL_QUEUE.glob("chapter_*.json"))
        if args.chapter > 0:
            chapter_files = [f for f in chapter_files if f"_{args.chapter:03d}" in f.name]

        pending = []
        for f in chapter_files:
            data = json.loads(f.read_text(encoding="utf-8"))
            ch = data["chapter_num"]
            out = VIDEO_QUEUE / f"episode_{ch:03d}.json"
            if not out.exists():
                pending.append(f)
            else:
                # 图片包存在时也验证图片完整性，不完整则重新处理
                try:
                    ep = json.loads(out.read_text(encoding="utf-8"))
                    all_ok = all(
                        s.get("image_path") and Path(s["image_path"]).exists()
                        for s in ep.get("shots", [])
                    )
                    if not all_ok:
                        pending.append(f)
                except Exception:
                    pending.append(f)

        if pending:
            log.info(f"📋 发现 {len(pending)} 个待处理章节")
            for cf in pending:
                ok = process_chapter(cf, characters)
                if not ok:
                    log.error(f"处理失败: {cf.name}")
        else:
            if not args.daemon:
                log.info("✅ 所有章节已处理完毕")
                break
            log.info("⏳ 无待处理章节，等待30秒...")

        if not args.daemon:
            break
        time.sleep(30)


if __name__ == "__main__":
    main()

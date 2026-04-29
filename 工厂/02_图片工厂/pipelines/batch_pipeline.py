# -*- coding: utf-8 -*-

# Step10: Real-ESRGAN upscaling 480p to 1080p

import argparse, json, logging, os, re, sqlite3, subprocess, sys, time, requests, urllib.parse
from difflib import SequenceMatcher

os.environ["NO_PROXY"] = os.environ.get("NO_PROXY", "") + ",localhost,127.0.0.1"
from quality_feedback import get_prompt_patches, log_issue
from api_tracker import tracker, CircuitOpen
from toonflow_agent_client import run_storyboard_agent_sync
from character_config import get_identity_cards, get_novel_name, get_genre, get_director_role, get_synopsis
from runtime_paths import get_config_path, get_latest_pipeline_state_path, get_pipeline_state_path, get_prompt_trace_path, get_quality_logs_dir, get_style_profiles_path, load_yaml_config, read_json, write_json

FFMPEG_BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "Layer_5_Engines", "tools", "ffmpeg", "bin")
if os.path.exists(FFMPEG_BIN):
    os.environ["PATH"] = FFMPEG_BIN + os.pathsep + os.environ.get("PATH", "")
    logger = logging.getLogger(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs", "pipeline.log"), encoding="utf-8")]
)
logger = logging.getLogger(__name__)

BASE          = "http://localhost:60000"
DB_PATH       = os.path.join(os.environ["APPDATA"], "toonflow-app", "db.sqlite")
PROJECT_ID    = 3  # Project ID
STYLE_PRESETS = {
    "dark_eastern_anime": {
        "video": "Generate video in dark eastern anime style, high contrast dramatic lighting, anime-style faces, eastern fantasy elements, mysterious atmosphere, no text overlays",
        "sb":    ", score_9, score_8_up, score_7_up, score_6_up, anime style, 2d, flat cel shading, thick black outlines, sharp lineart, mappa anime, seinen, dark anime, dark_background, eerie, horror",
    },
    "realistic": {
        "video": "Generate video in cinematic realistic style, rich saturated colors, VFX, film-quality cinematography, no text overlays",
        "sb":    ", realistic photographic style, rich saturated colors, cinematic quality, high saturation, rich jewel tones, dramatic color grading, no text, no watermark, no Chinese characters",
    },
    "anime": {
        "video": "Generate video in high-quality Japanese anime style, detailed artwork, anime faces with expressive eyes, dramatic lighting, vibrant colors, no text overlays",
        "sb":    ", anime style, Japanese animation aesthetic, sharp expressive eyes, dramatic expressions, vibrant colors, detailed character design, cinematic lighting, dynamic camera angles, high quality, no text",
    },
    "3d": {
        "video": "Generate video in 3D animation style, no text overlays",
        "sb":    ", 3D render style, ultra-realistic, no text",
    },
}
# Lazy init: style profile loaded after get_style_profile is defined
_3d_profile = None
VIDEO_PREFIX  = STYLE_PRESETS["realistic"]["video"]
SB_SUFFIX     = STYLE_PRESETS["realistic"]["sb"]
VISUAL_STYLE_ANCHOR = "3D_render, chinese_anime, xianxia_fantasy, highly_detailed, cinematic_lighting, volumetric_fog, mystical_atmosphere, eastern_fantasy_architecture"
VISUAL_NEGATIVE = "lowres, bad anatomy, bad hands, text, error, missing fingers, extra digit, fewer digits, cropped, worst quality, low quality, normal quality, jpeg artifacts, signature, watermark"
DEEPSEEK_URL  = "https://api.deepseek.com/v1/chat/completions"
IMAGE_BACKEND = "gpt_image2"  # gpt_image2 | local_comfyui | qwen_edit_local | nvidia_flux | nano_banana | gemini_flash | fal_seedream4

STYLE_PROFILE_PATH = str(get_style_profiles_path())
_style_profile_cache = None

CHARACTERS = {}
CHARACTER_IDENTITY_CARDS = get_identity_cards()
_settings_cache = {}

_prompt_trace_context = {"chapter": None, "title": None, "path": None}

def _set_prompt_trace_context(chapter: int, title: str = ""):
    # [docstring removed]
    global _prompt_trace_context
    _prompt_trace_context = {
        "chapter": chapter,
        "title": title,
        "path": get_prompt_trace_path(chapter, title)
    }

def _empty_prompt_trace():
    # [docstring removed]
    return {
        "chapter": _prompt_trace_context["chapter"],
        "title": _prompt_trace_context["title"],
        "created_at": int(time.time()),
        "categories": {}
    }

def _load_prompt_trace():
    # [docstring removed]
    if not _prompt_trace_context["path"]:
        return _empty_prompt_trace()
    return read_json(_prompt_trace_context["path"], _empty_prompt_trace())

def _save_prompt_trace(data):
    # [docstring removed]
    if _prompt_trace_context["path"]:
        write_json(_prompt_trace_context["path"], data)

def _upsert_prompt_trace(category: str, key: str, data: dict):
    """更新或插?prompt trace 记录"""
    trace = _load_prompt_trace()
    if category not in trace["categories"]:
        trace["categories"][category] = {}
    trace["categories"][category][key] = {
        **data,
        "updated_at": int(time.time())
    }
    _save_prompt_trace(trace)

def _normalize_prompt_text(text: str) -> str:
    # [docstring removed]
    if not text:
        return ""
    return " ".join(text.split())

def _build_prompt_versions(
    raw_prompt: str,
    required_terms: list[str] = None,
    positive_patch: str = "",
    negative_patch: str = "",
    final_suffix: str = "",
) -> dict:
    # [docstring removed]
    normalized = _normalize_prompt_text(raw_prompt)
    polished = normalized
    if positive_patch and positive_patch.lower() not in polished.lower():
        polished = f"{polished}{positive_patch}" if polished else positive_patch
    if required_terms:
        for term in required_terms:
            if term.lower() not in polished.lower():
                polished = f"{polished}, {term}" if polished else term
    final_prompt = polished
    if final_suffix:
        final_prompt = f"{final_prompt}{final_suffix}" if final_prompt else final_suffix
    return {
        "raw": raw_prompt,
        "polished": polished,
        "final": final_prompt,
        "patch_positive": positive_patch,
        "patch_negative": negative_patch,
    }


def _default_style_profile():
    return {
        "video_prefix": VIDEO_PREFIX,
        "storyboard_suffix": SB_SUFFIX,
        "visual_style_anchor": VISUAL_STYLE_ANCHOR,
        "visual_negative": VISUAL_NEGATIVE,
        "asset_prompts": {
            "scene": "{visual_style_anchor}, wide establishing shot environment illustration, cinematic composition, dramatic lighting, oppressive mood, {name}, {desc}, detailed background painting, desaturated palette, deep-red accents only, no photorealism, no product-photo look, no text, no watermark",
            "prop": "{visual_style_anchor}, single mystical prop illustration, centered prop design sheet, dark textured background, {name}, {desc}, anime prop painting, sharp silhouette, detailed material texture, desaturated palette, deep-red accents only, not a product photo, no hand holding item, no text, no watermark"
        }
    }


def _merge_style_profile(base, override):
    merged = dict(base or {})
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            child = dict(merged[k])
            child.update(v)
            merged[k] = child
        else:
            merged[k] = v
    return merged


def get_style_profile(project_id=PROJECT_ID):
    global _style_profile_cache
    if _style_profile_cache and _style_profile_cache.get("_project_id") == project_id:
        return _style_profile_cache
    profile = _default_style_profile()
    if os.path.exists(STYLE_PROFILE_PATH):
        try:
            with open(STYLE_PROFILE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
            profile = _merge_style_profile(profile, data.get("default", {}))
            profile = _merge_style_profile(profile, (data.get("projects", {}) or {}).get(str(project_id), {}))
        except Exception as e:
            logger.warning(f"风格profile加载失败: {e}")
    profile["_project_id"] = project_id
    _style_profile_cache = profile
    return profile


def _style_text(key, default=""):
    return get_style_profile(PROJECT_ID).get(key, default)


def _base_pipeline_state(args):
    cfg = load_yaml_config()
    return {
        "chapter": args.chapter,
        "title": args.title,
        "novel_path": args.novel or "",
        "from_step": args.from_step,
        "to_step": args.to_step,
        "project_id": PROJECT_ID,
        "status": "running",
        "current_step": 0,
        "outline_id": args.outline_id,
        "script_id": args.script_id,
        "image_backend": getattr(args, "image_backend", None) or IMAGE_BACKEND,
        "video_backend": getattr(args, "video_backend", None),
        "render_mode": getattr(args, "render_mode", None),
        "style": getattr(args, "style", ""),
        "config_path": cfg.get("_meta", {}).get("config_path", str(get_config_path())),
        "updated_at": int(time.time()),
        "steps": {},
    }


def _save_pipeline_state(state):
    state["updated_at"] = int(time.time())
    write_json(get_pipeline_state_path(state["chapter"], state.get("title", "")), state)
    write_json(get_latest_pipeline_state_path(), state)


def _mark_pipeline_step(state, step_no, name, status, **extra):
    steps = state.setdefault("steps", {})
    step = steps.setdefault(str(step_no), {"name": name})
    step["name"] = name
    step["status"] = status
    step["updated_at"] = int(time.time())
    step.update(extra)
    state["current_step"] = max(int(state.get("current_step") or 0), int(step_no))
    if extra.get("outline_id"):
        state["outline_id"] = extra["outline_id"]
    if extra.get("script_id"):
        state["script_id"] = extra["script_id"]
    _save_pipeline_state(state)


def _asset_style_prompt(asset_type, **kwargs):
    profile = get_style_profile(PROJECT_ID)
    templates = profile.get("asset_prompts", {}) or {}
    template = templates.get(asset_type, "")
    values = {
        "visual_style_anchor": profile.get("visual_style_anchor", VISUAL_STYLE_ANCHOR),
        "scene_visual_anchor": profile.get("scene_visual_anchor", profile.get("visual_style_anchor", VISUAL_STYLE_ANCHOR)),
        "name": kwargs.get("name", ""),
        "desc": kwargs.get("desc", "")
    }
    return template.format(**values) if template else ""


def _clean_scene_description_for_env(desc, scene_name=""):
    """Clean scene description: keep environment keywords, remove character actions/names."""
    text = (desc or "").replace("血", "深红").replace("杀", "冲突")
    if not text:
        return scene_name or "abandoned cursed hall, ruined stone architecture, damp air, eerie candlelight"
    noise_patterns = [
        r"[\u4e00-\u9fff]{1,4}(说|道|喊|叫|怒|笑|哭|叹)",
        r"(他|她|它|其)(们)?",
        r"(突然|忽然|猛地|随即|于是)",
    ]
    for pattern in noise_patterns:
        text = re.sub(pattern, "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:200] if text else (scene_name or "mysterious environment")
def _load_story_facts(chapter_num):
    quality_dir = str(get_quality_logs_dir())
    path = os.path.join(quality_dir, f"chapter{chapter_num}_story_facts.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        shots = data.get("shots", []) if isinstance(data, dict) else []
        return {int(item.get("shot")): item for item in shots if isinstance(item, dict) and item.get("shot") is not None}
    except (json.JSONDecodeError, OSError, ValueError) as e:
        logger.warning(f"  剃事实取? {e}")
        return {}


def _format_story_fact_rules(story_fact):
    """Format story fact rules for prompt generation."""
    if not story_fact:
        return ""
    must_have = story_fact.get("must_have", {})
    primary_characters = ", ".join(must_have.get("primary_characters", [])) or "无"
    secondary_characters = ", ".join(must_have.get("secondary_characters", [])) or "无"
    locations = ", ".join(must_have.get("location", [])) or "无"
    actions = ", ".join(must_have.get("actions", [])) or "无"
    props = ", ".join(must_have.get("props", [])) or "无"
    composition = ", ".join(must_have.get("composition", [])) or "无"
    must_not = ", ".join(story_fact.get("must_not_have", [])) or "无"
    notes = story_fact.get("notes", "") or ""
    event_type = story_fact.get("event_type", "") or ""
    return f"""
"""
def _storyboard_scene_key(scene_title, scene_desc=""):
    """Map scene title/desc to a known scene key for hard-coded templates."""
    text = f"{scene_title} {scene_desc}".lower()
    mapping = [
        (["仪式", "开幕", "典礼"], "ceremony_opening"),
        (["宣布", "规则", "公告"], "announce_rules"),
        (["擂台", "对战", "比武"], "arena_fight"),
        (["秦风", "挑战", "对决"], "qinfeng_challenge"),
    ]
    for keywords, key in mapping:
        if any(word in text for word in keywords):
            return key
    return ""
def _get_storyboard_hard_template(chapter_num, scene_title, scene_desc=""):
    """Return hard-coded storyboard template for specific scenes (chapter 36 only)."""
    if chapter_num != 36:
        return ""
    scene_key = _storyboard_scene_key(scene_title, scene_desc)
    # Hard-coded templates removed due to encoding corruption
    # Return empty to fall back to dynamic generation
    return ""
def _get_fixed_storyboard_prompt(chapter_num, scene_title, scene_desc=""):
    """Return hard-coded storyboard prompt for specific scenes (chapter 36 only)."""
    if chapter_num != 36:
        return ""
    scene_key = _storyboard_scene_key(scene_title, scene_desc)
    # Hard-coded prompts removed due to encoding corruption
    # Return empty to fall back to dynamic generation
    return ""
def _load_characters_from_db(s_http=None):
    """Load character dict from ToonFlow DB, keyed by name."""
    try:
        conn = get_db()
        rows = conn.execute(
            "SELECT name, intro FROM t_assets WHERE type='角色' AND projectId=?",
            (PROJECT_ID,)
        ).fetchall()
        conn.close()
        result = {r[0]: r[1] or "" for r in rows if r[0]}
        if result:
            return result
    except Exception as e:
        logger.warning(f"_load_characters_from_db failed: {e}")
    # Fallback to characters.json
    raw = _load_characters_raw()
    return {name: card.get("intro", "") for name, card in raw.items()} if raw else {}

def _load_characters_raw():
    # [docstring removed]
    from character_config import _load as _cc_load
    data = _cc_load()
    return data.get("characters", {})

def _default_protagonist():
    """Return default protagonist name."""
    if CHARACTER_IDENTITY_CARDS:
        return next(iter(CHARACTER_IDENTITY_CARDS))
    if CHARACTERS:
        return next(iter(CHARACTERS))
    return "主角"
def _character_names_str():
    """Return slash-separated character names string for prompts."""
    names = list(CHARACTER_IDENTITY_CARDS.keys()) if CHARACTER_IDENTITY_CARDS else (list(CHARACTERS.keys()) if CHARACTERS else [])
    return "/".join(names[:6]) if names else "主角"
def _body_type_summaries():
    # [docstring removed]
    parts = []
    for name, card in CHARACTER_IDENTITY_CARDS.items():
        body = card.get("body", "")
        age = card.get("age", "")
        brief = f"{body[:6]}{age[:4]}" if body else age[:10]
        parts.append(f"{name}={brief}")

PREFERRED_VOLC_VIDEO_PATTERNS = [
    "seedance-1-5", "seedance 1.5", "1.5",
    "seedance-1-1-0", "seedance 1.1.0", "1.1.0",
]
PREFERRED_VOLC_IMAGE_PATTERNS = [
    "seedream-5-0", "seedream-4-5", "seedream",  # 灱引擎云优先
    "v1-5-pruned-emaonly", "comfyui", "sd1.5",   # ComfyUI 朜备用
]

def get_db():
    return sqlite3.connect(DB_PATH, timeout=30)

_settings_cache = None
def _get_settings(s_http=None):
    # [docstring removed]
    global _settings_cache
    if _settings_cache is not None:
        return _settings_cache
    if s_http:
        try:
            r = s_http.post(BASE + "/setting/getSetting", json={}, timeout=10)
            if r.status_code == 200:
                data = r.json().get("data", [])
                if isinstance(data, list) and data:
                    _settings_cache = data
                    return data
        except (requests.RequestException, json.JSONDecodeError, KeyError) as e:
            logger.warning(f"  getSetting API失败, 降级到DB: {e}")
    # DB 兜底
    conn = get_db()
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM t_config ORDER BY id").fetchall()
    conn.close()
    _settings_cache = [dict(r) for r in rows] if rows else []
    return _settings_cache

def get_deepseek_key(s_http=None):
    settings = _get_settings(s_http)
    for s in settings:
        key = s.get("apiKey", "")
        if key and key.startswith("sk-") and s.get("manufacturer") == "deepSeek":
            return key
    for s in settings:
        key = s.get("apiKey", "")
        if key and key.startswith("sk-"):
            return key
    return None

def get_ai_config_id(s_http=None):
    settings = _get_settings(s_http)
    for s in settings:
        if s.get("type") == "text" and s.get("manufacturer") == "deepSeek":
            return s.get("id", 1)
    # 任何 text 类型
    for s in settings:
        if s.get("type") == "text":
            return s.get("id", 1)
    return settings[0].get("id", 1) if settings else 1

def _pick_preferred_config(rows, patterns, env_name=None):
    forced = (os.environ.get(env_name or "", "") or "").strip().lower() if env_name else ""
    preferred_patterns = [forced] if forced else [p.lower() for p in patterns]
    for pattern in preferred_patterns:
        for row in rows:
            model = (row["model"] or "").lower()
            if pattern and pattern in model:
                return row
    return None

def get_video_ai_config_id(s_http=None):
    # [docstring removed]
    settings = _get_settings(s_http)
    video_cfgs = [s for s in settings if s.get("type") == "video" or s.get("modelType") == "video"
                or "seedance" in (s.get("model") or "").lower()]
    row = _pick_preferred_config(video_cfgs, PREFERRED_VOLC_VIDEO_PATTERNS, "VOLC_FREE_VIDEO_MODEL")
    if not row:
        row = next((r for r in video_cfgs if "seedance" in (r.get("model") or "").lower() and "pro" not in (r.get("model") or "").lower()), None)
    if not row:
        row = next((r for r in video_cfgs if "seedance" in (r.get("model") or "").lower()), None)
    if row:
        return row.get("id", 4)
    import sqlite3 as _sql
    _conn = get_db()
    _conn.row_factory = _sql.Row
    _rows = _conn.execute(
        "SELECT id, model FROM t_config WHERE type='video' OR model LIKE '%seedance%' ORDER BY id DESC"
    ).fetchall()
    _conn.close()
    if _rows:
        _r = next((x for x in _rows if "seedance" in (x["model"] or "").lower()), _rows[0])
        return _r["id"]
    return 4

def get_role_appearances(s_http=None):
    """Get role appearance descriptions from ToonFlow API/DB, fallback to characters.json."""
    if s_http:
        try:
            r = s_http.post(BASE + "/assets/getAssets", json={"projectId": PROJECT_ID, "type": "role"}, timeout=10)
            if r.status_code == 200:
                data = r.json().get("data", [])
                if isinstance(data, list):
                    result = {}
                    for a in data:
                        name = a.get("name", "")
                        vp = a.get("videoPrompt", "")
                        if name and vp:
                            result[name] = vp
                    if result:
                        return result
        except Exception:
            pass
    conn = get_db()
    rows = conn.execute(
        "SELECT name, videoPrompt FROM t_assets WHERE type='role' AND videoPrompt IS NOT NULL AND projectId=?",
        (PROJECT_ID,)
    ).fetchall()
    conn.close()
    result = {r[0]: r[1] for r in rows if r[1]}
    if not result:
        # fallback: use characters.json identity cards
        for name, card in CHARACTER_IDENTITY_CARDS.items():
            desc = f"{card.get('age','')}, {card.get('hair','')}, {card.get('clothing','')[:30]}, {card.get('face','')}"
            result[name] = desc
    return result
def deepseek_call(key, messages, model="deepseek-chat", max_tokens=2000):
    _sess = requests.Session()
    r = _sess.post(DEEPSEEK_URL, json={
        "model": model, "messages": messages, "max_tokens": max_tokens, "temperature": 0.7
    }, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, timeout=60)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]

def import_novel_to_toonflow(s, chapter_num, chapter_title, novel_text):
    """Import novel chapter text into ToonFlow DB."""
    logger.info("Step 0: Import novel to ToonFlow")
    chapter_name = f"第{chapter_num}章 {chapter_title}"
    try:
        conn = get_db()
        existing = conn.execute(
            "SELECT id, chapter, length(chapterData) as len FROM t_novel WHERE projectId=? AND chapter LIKE ?",
            (PROJECT_ID, f"第{chapter_num}章%")
        ).fetchone()
        conn.close()
        if existing:
            if existing[1] == chapter_name:
                logger.info(f"  Chapter already exists (id={existing[0]}, {existing[2]} chars), skip")
            else:
                conn = get_db()
                conn.execute(
                    "UPDATE t_novel SET chapter=?, chapterData=? WHERE id=?",
                    (chapter_name, novel_text, existing[0])
                )
                conn.commit()
                conn.close()
                logger.info(f"  Updated chapter: {existing[1]} -> {chapter_name} (id={existing[0]})")
            return
    except Exception:
        pass
    payload = {
        "projectId": PROJECT_ID,
        "data": [{
            "index":       chapter_num,
            "reel":        "default",
            "chapter":     chapter_name,
            "chapterData": novel_text
        }]
    }
    r = s.post(BASE + "/novel/addNovel", json=payload, timeout=30)
    if r.status_code == 200:
        logger.info(f"  Novel imported: {chapter_name} ({len(novel_text)} chars)")
    else:
        logger.warning(f"  Import failed: HTTP {r.status_code}: {r.text[:100]}")
def read_novel(path):
    """Read novel text from file, trying multiple encodings."""
    for enc in ["utf-8", "gb18030", "gbk", "utf-16"]:
        try:
            with open(path, encoding=enc) as f:
                return f.read().strip()
        except Exception:
            continue
    raise ValueError(f"Cannot read file: {path}")
def create_outline(s, chapter_num, chapter_title, novel_text, key):
    """Step 1: Use DeepSeek to generate chapter outline JSON."""
    logger.info("Step 1: Generate outline (DeepSeek)")
    _novel = get_novel_name()
    _role = get_director_role()
    outline_prompt = f"""你是{_role}，根据以下《{_novel}》第{chapter_num}章内容生成大纲。
{novel_text[:1500]}


{{
  "shots": [{{"title": "...", "desc": "...", "location": "...", "roles": ["{_default_protagonist()}"], "mood": "...", "intensity": "...", "element": "..."}}],
  "scenes": [{{"name": "...", "description": "..."}}, ...],
  "characters": [{{"name": "...", "description": "..."}}, ...],
  "props": [{{"name": "...", "description": "..."}}, ...],
  "outline": "...",
  "keyEvents": ["...", "...", "...", "..."],
  "emotionalCurve": "...",
  "openingHook": "...",
  "endingHook": "..."
}}
只输出JSON，不要其他文字。"""

    content = deepseek_call(key, [{"role": "user", "content": outline_prompt}])

    # Strip markdown code fences
    content = re.sub(r'```(?:json)?\s*', '', content).strip()

    # Extract JSON (error handling)
    outline_obj = None
    match = re.search(r'\{.*\}', content, re.DOTALL)
    if match:
        raw_json = match.group()
        raw_json = raw_json.replace("\uff0c", ",").replace("\uff1a", ":").replace("\u2018", '"').replace("\u2019", '"')
        raw_json = re.sub(r',\s*]', ']', raw_json)
        raw_json = re.sub(r',\s*,', ',', raw_json)
        try:
            outline_obj = json.loads(raw_json)
        except json.JSONDecodeError as e:
            logger.warning(f"  JSON parse failed: {e}")
            logger.warning(f"  Raw content: {content[:300]}")

    # Compat: if LLM outputs array instead of object, auto-convert
    if outline_obj is None:
        match2 = re.search(r'\[.*\]', content, re.DOTALL)
        if match2:
            raw_json2 = match2.group().replace("\uff0c", ",").replace("\uff1a", ":").replace("\u2018", '"').replace("\u2019", '"')
            raw_json2 = re.sub(r',\s*]', ']', raw_json2)
            raw_json2 = re.sub(r',\s*,', ',', raw_json2)
            try:
                old_scenes = json.loads(raw_json2)
                outline_obj = {"shots": old_scenes}
                logger.info("  Compat: converted old array format")
            except json.JSONDecodeError:
                pass

    if not outline_obj:
        logger.warning("  Using default outline (8 shots)")
        outline_obj = {"shots": [{"title": f"场景{i+1}", "desc": novel_text[i*200:(i+1)*200][:100],
                    "location": "", "roles": [_default_protagonist()], "mood": "紧张"} for i in range(min(8, max(1, len(novel_text)//200)))]}

    # Extract fields
    shots = outline_obj.get("shots", [])
    llm_scenes = outline_obj.get("scenes", [])
    llm_chars = outline_obj.get("characters", [])
    llm_props = outline_obj.get("props", [])
    llm_outline = outline_obj.get("outline", "")
    llm_key_events = outline_obj.get("keyEvents", [])
    llm_emotional_curve = outline_obj.get("emotionalCurve", "")
    llm_opening_hook = outline_obj.get("openingHook", "")
    llm_ending_hook = outline_obj.get("endingHook", "")

    scene_candidates = _collect_scene_asset_candidates(llm_scenes)
    if scene_candidates:
        llm_scenes = [{"name": item["name"], "description": item["description"]} for item in scene_candidates]
        logger.info(f"  Filtered LLM scenes: {len(llm_scenes)} real spaces")
    else:
        scene_candidates = _collect_scene_asset_candidates(shots)
        llm_scenes = [{"name": item["name"], "description": item["description"]} for item in scene_candidates]
        logger.info(f"  Auto-extracted from shots/location: {len(llm_scenes)} spaces")

    # Ensure scenes is never empty — derive from shots if needed
    if not llm_scenes and shots:
        llm_scenes = [{"name": s.get("title", s.get("location", f"场景{i+1}")),
                       "description": s.get("desc", "")} for i, s in enumerate(shots)]
        logger.info(f"  Fallback: derived {len(llm_scenes)} scenes from shots")

    # If LLM didn't provide characters, use local CHARACTERS
    if not llm_chars:
        llm_chars = [{"name": k, "description": v} for k, v in CHARACTERS.items()]

    # If LLM didn't provide props, use IDENTITY_CARDS
    if not llm_props:
        llm_props = [{"name": card.get("props","").split(",")[0].split("，")[0].strip(),
                      "description": card.get("props","")}
                     for card in CHARACTER_IDENTITY_CARDS.values()
                     if card.get("props")]

    # Build ToonFlow outline data
    tf_scenes = [{"name": s.get("title", f"场景{s.get('scene','')}"),
                  "description": s.get("desc", ""),
                  "location": s.get("location", ""),
                  "roles": s.get("roles", [_default_protagonist()]),
                  "mood": s.get("mood", "紧张"),
                  "intensity": s.get("intensity", "🟡"),
                  "element": s.get("element", "")} for s in shots]

    # Build outline text fallbacks
    if not llm_outline:
        llm_outline = " → ".join(s.get("title","") for s in shots)
    if not llm_key_events:
        llm_key_events = [s.get("title","") for s in shots[:4]] if len(shots) >= 4 else [s.get("title","") for s in shots]
    if not llm_emotional_curve:
        llm_emotional_curve = " → ".join(f"{s.get('mood','')}{s.get('intensity','')}" for s in shots if s.get("mood"))
    if not llm_opening_hook:
        llm_opening_hook = shots[0].get("desc","") if shots else ""
    if not llm_ending_hook:
        llm_ending_hook = shots[-1].get("desc","") if shots else ""

    outline_data = json.dumps({
        "episodeIndex": chapter_num,
        "title": chapter_title,
        "chapterRange": [chapter_num],
        "scenes": llm_scenes,
        "characters": llm_chars,
        "props": llm_props,
        "coreConflict": "",
        "outline": llm_outline,
        "openingHook": llm_opening_hook,
        "keyEvents": llm_key_events,
        "emotionalCurve": llm_emotional_curve,
        "visualHighlights": [],
        "endingHook": llm_ending_hook,
        "classicQuotes": [],
        "_shots": tf_scenes
    }, ensure_ascii=False)

    # Dedup: check if same episode already has outline
    conn = get_db()
    existing = conn.execute(
        "SELECT id FROM t_outline WHERE projectId=? AND episode=?",
        (PROJECT_ID, chapter_num)
    ).fetchone()
    conn.close()

    if existing:
        outline_id = existing[0]
        conn2 = get_db()
        conn2.execute("UPDATE t_outline SET data=? WHERE id=?", (outline_data, outline_id))
        conn2.commit()
        conn2.close()
        logger.info(f"  Reused existing outline_id={outline_id}")
    else:
        r = s.post(BASE + "/outline/addOutline", json={
            "projectId": PROJECT_ID,
            "episode":   chapter_num,
            "data":      outline_data
        })
        if r.status_code != 200:
            raise RuntimeError(f"addOutline failed: {r.text[:200]}")
        conn3 = get_db()
        row = conn3.execute(
            "SELECT id FROM t_outline WHERE projectId=? AND episode=? ORDER BY id DESC LIMIT 1",
            (PROJECT_ID, chapter_num)
        ).fetchone()
        if not row:
            import time as _t; _t.sleep(1)
            row = conn3.execute(
                "SELECT id FROM t_outline WHERE projectId=? ORDER BY id DESC LIMIT 1",
                (PROJECT_ID,)
            ).fetchone()
        conn3.close()
        if not row:
            raise RuntimeError("addOutline API succeeded but DB record not found")
        outline_id = row[0]
    logger.info(f"  outline_id={outline_id}, {len(shots)} shots, {len(llm_scenes)} scenes")
    return outline_id, tf_scenes
def create_script_record(outline_id, chapter_num, chapter_title):
    """Step 2: Create script record in DB."""
    logger.info("Step 2: Create script record (DB insert)")
    name = f"第{chapter_num}章 {chapter_title}"
    conn = get_db()
    # Dedup: reuse if same-name script exists
    existing = conn.execute(
        "SELECT id FROM t_script WHERE name=? AND projectId=?", (name, PROJECT_ID)
    ).fetchone()
    if existing:
        script_id = existing[0]
        conn.execute("UPDATE t_script SET outlineId=? WHERE id=?", (outline_id, script_id))
        conn.commit()
        conn.close()
        logger.info(f"  Reused existing script_id={script_id}")
        return script_id
    c = conn.cursor()
    c.execute(
        "INSERT INTO t_script (name, content, projectId, outlineId) VALUES (?,?,?,?)",
        (name, "", PROJECT_ID, outline_id)
    )
    conn.commit()
    script_id = c.lastrowid
    conn.close()
    logger.info(f"  Created script_id={script_id}")
    return script_id
def _get_script_content(s, script_id):
    # [docstring removed]
    try:
        r = s.post(BASE + "/script/geScriptApi", json={"projectId": PROJECT_ID}, timeout=10)
        if r.status_code == 200:
            scripts = r.json().get("data", [])
            if isinstance(scripts, list):
                for sc in scripts:
                    if sc.get("id") == script_id:
                        return sc.get("content", "") or ""
    except Exception:
        pass
    conn = get_db()
    row = conn.execute("SELECT content FROM t_script WHERE id=?", (script_id,)).fetchone()
    conn.close()
    return (row[0] or "") if row else ""


def _get_outline_storyboard_shots(outline_data):
    if not isinstance(outline_data, dict):
        return []
    return outline_data.get("_shots") or outline_data.get("shots") or outline_data.get("scenes") or []

def generate_script(s, outline_id, script_id):
    logger.info("Step 3: AI生成剜内 (generateScriptApi, ~30s)")
    existing = _get_script_content(s, script_id)
    if len(existing) > 100:
        logger.info(f"  ️ 剜已有 {len(existing)} 字,跳过重新生成")
        s.post(BASE + "/script/generateScriptSave", json={
                "outlineId": outline_id, "content": existing, "scriptId": script_id
            }, timeout=10)
        # [syntax fixed]
        pass
        return existing

    try:
        r = s.post(BASE + "/script/generateScriptApi", json={
            "outlineId": outline_id,
            "scriptId":  script_id
        }, timeout=120)
        if r.status_code != 200 or "大纲为空" in r.text:
            logger.warning(f"  generateScriptApi returned: {r.text[:200]}, will use outline data as script")
    except Exception as e:
        logger.warning(f"  generateScriptApi exception: {e}")

    content = _get_script_content(s, script_id)

    s.post(BASE + "/script/generateScriptSave", json={
            "outlineId": outline_id,
            "content": content,
            "scriptId": script_id
        }, timeout=10)
    # [syntax fixed]
    pass
    return content

def generate_storyboard_prompts(key, script_content, scenes, role_appearances, chapter_num=None):
    logger.info(f"Step 4: Generate storyboard prompts (DeepSeek, {len(scenes)} scenes)")

    char_ref = "\n".join(f"- {k}: {v[:60]}" for k, v in CHARACTERS.items())
    prompts  = []
    if chapter_num is None:
        chapter_match = re.search(r"第(\d+)章", script_content or "")
        chapter_num = int(chapter_match.group(1)) if chapter_match else None
    story_facts = _load_story_facts(chapter_num) if chapter_num else {}
    storyboard_patches = get_prompt_patches("storyboard")
    patch_positive = storyboard_patches.get("positive") or ""
    patch_negative = storyboard_patches.get("negative") or ""

    outline_scene_desc_map = {
        (sc.get("name", "") or "").strip(): (sc.get("description", "") or "").strip()
        for sc in _get_outline_scenes()
        if (sc.get("name", "") or "").strip()
    }

    # Build character description map from DB intro
    name_to_desc = {}
    for cname, cintro in CHARACTERS.items():
        # Use first 30 chars of intro as appearance summary
        name_to_desc[cname] = cintro[:30] if cintro else cname

    role_constraints = []
    for cname, cdesc in CHARACTERS.items():
        role_constraints.append(f"- {cname}: {cdesc[:80]}")
    role_constraints_text = "\n".join(role_constraints)

    # ── Pre-allocate shot types (visual diversity) ─────────────────
    _SHOT_PLAN = []
    n = len(scenes)
    if n > 0:
        # Target distribution: close-up 15%, close 25%, medium 35%, long 20%, extreme-long 5%
        _shot_types = (
            ["特写"] * max(1, round(n * 0.15)) +
            ["近景"] * max(1, round(n * 0.25)) +
            ["中景"] * max(1, round(n * 0.35)) +
            ["远景"] * max(1, round(n * 0.20)) +
            ["大远景"] * max(0, round(n * 0.05))
        )
        # Detect scene hints for preferred shot type
        _scene_hints = []
        for sc in scenes:
            _st = f"{sc.get('title', '')} {sc.get('desc', '') or sc.get('description', '') or ''}".lower()
            if any(t in _st for t in ["远景", "大远景", "全景", "俯瞰", "鸟瞰", "远眺", "大场面"]):
                _scene_hints.append("远景")
            elif any(t in _st for t in ["特写", "近景", "对峙", "对视", "怒视", "哭泣"]):
                _scene_hints.append("近景")
            else:
                _scene_hints.append(None)
        # Assign hints first, then fill remaining randomly
        _assigned = [None] * n
        _remaining = list(_shot_types)
        for i, hint in enumerate(_scene_hints):
            if hint == "远景":
                for rt in ["远景", "大远景", "中景"]:
                    if rt in _remaining:
                        _assigned[i] = rt; _remaining.remove(rt); break
            elif hint == "近景":
                for rt in ["特写", "近景", "中景"]:
                    if rt in _remaining:
                        _assigned[i] = rt; _remaining.remove(rt); break
        # Fill unassigned randomly
        import random as _rnd
        _rnd.shuffle(_remaining)
        for i in range(n):
            if _assigned[i] is None and _remaining:
                _assigned[i] = _remaining.pop(0)
            elif _assigned[i] is None:
                _assigned[i] = "中景"
        _SHOT_PLAN = _assigned
        logger.info(f"  Shot plan: {', '.join(f'{i+1}={t}' for i, t in enumerate(_SHOT_PLAN))}")

    for idx, sc in enumerate(scenes, start=1):
        sc_title = sc.get('title') or sc.get('name') or f"Scene {sc.get('scene','')}" or "Scene"
        sc_location = (sc.get('location') or '').strip()
        sc_space_desc = outline_scene_desc_map.get(sc_location, "") if sc_location else ""
        sc_desc = sc.get('desc') or sc.get('description') or sc.get('content') or sc_title
        sc_roles = sc.get('roles') or sc.get('characters') or [_default_protagonist()]
        if sc_roles and isinstance(sc_roles[0], dict):
            sc_roles = [r.get('name', _default_protagonist()) for r in sc_roles]
        sc_mood = sc.get('mood') or sc.get('emotion') or 'tense'
        story_fact = story_facts.get(idx) or {}
        story_fact_rules = _format_story_fact_rules(story_fact)
        hard_template_rules = _get_storyboard_hard_template(
            chapter_num,
            f"{sc_location} {sc_title}".strip(),
            f"{sc_space_desc} {sc_desc}".strip(),
        )
        fixed_prompt = _get_fixed_storyboard_prompt(
            chapter_num,
            f"{sc_location} {sc_title}".strip(),
            f"{sc_space_desc} {sc_desc}".strip(),
        )

        # Build character identity lines for the prompt
        identity_lines = []
        ban_all = []
        for r in sc_roles[:2]:
            card = CHARACTER_IDENTITY_CARDS.get(r)
            if card:
                _clothing_en = card.get('clothing_prompt_override') or card.get('clothing', '')
                identity_lines.append(
                    f"- {r}({card['age']}): {card['body']}, {card['face']}, {card['hair']}, "
                    f"clothing={_clothing_en}"
                    + (f", props={card['props']}" if card.get('props') else "")
                )
                ban_all.extend(card.get("ban_words", []))
            else:
                identity_lines.append(f"- {r}: {CHARACTERS.get(r, r)[:80]}")
        allowed_roles_text = "\n".join(identity_lines)
        ban_words_text = ", ".join(sorted(set(ban_all))) if ban_all else ""
        scene_text = f"{sc_location} {sc_space_desc} {sc_title} {sc_desc}".lower()
        is_long_shot = any(token in scene_text for token in ["远景", "大远景", "全景", "俯瞰", "鸟瞰", "远眺", "鸟瞰", "极远"])
        is_close_shot = any(token in scene_text for token in ["近景", "特写", "对峙", "对视", "怒视", "面部", "表情", "哭泣", "凝视"])
        is_two_character = len(sc_roles[:2]) == 2
        confrontation_tokens = ["对峙", "对决", "决斗", "厮杀", "交锋", "搏斗", "激战", "对抗", "冲突", "较量"]
        is_confrontation = is_two_character and any(token in scene_text for token in confrontation_tokens)
        single_subject = len(sc_roles[:2]) <= 1

        # Shot-type specific composition rules
        long_shot_rules = (
            "- 远景/大远景：人物占画面不超过1/3，环境为主体\n"
            "- 用建筑/山川/天空展示场景规模感\n"
            "- 适合开场建立镜头、转场、气氛渲染\n"
            "- 相机角度可用：鸟瞰(bird's eye)、高角度俯拍(high angle)、水平远眺\n"
        ) if is_long_shot else ""
        close_shot_rules = (
            "- 近景/特写：人物面部或上半身占画面主体\n"
            "- 强调表情、眼神、情绪细节\n"
            "- 背景虚化(shallow depth of field)，聚焦角色\n"
            "- 适合情绪爆发、对话、内心独白\n"
            "- 相机角度：平视(eye level)或略仰(slight low angle)\n"
        ) if is_close_shot else ""
        confrontation_rules = (
            "- 双人对峙构图：两人分列画面左右两侧\n"
            "- 用对角线构图或对称构图强调对抗关系\n"
            "- 两人之间留出张力空间(negative space)\n"
            "- 可用过肩镜头(over-the-shoulder)增加临场感\n"
            "- 光影分明，主角侧暖光配角侧冷光\n"
        ) if is_confrontation else ""
        single_subject_rules = (
            "- 单人镜头：主体偏离画面中心(三分法)\n"
            "- 视线方向留出空间(look room)\n"
            "- 用环境元素(门框/树枝/岩石)形成自然画框\n"
            "- 避免正面证件照式呆板构图\n"
        ) if single_subject else ""

        shot_type = _SHOT_PLAN[idx-1] if _SHOT_PLAN else "中景"
        prompt_req = f"""为以下{get_genre()}场景生成{shot_type}**电影分镜**提示词（注意：这是剧情画面，不是角色展示图）,
{f'空间描述(大纲): {sc_space_desc}' if sc_space_desc else ''}

{allowed_roles_text}

**核心原则（最重要）:**
{close_shot_rules}
{confrontation_rules}
{single_subject_rules}
{story_fact_rules}
{hard_template_rules}
{f'- 禁止在画面中出现以下词汇或元素: {ban_words_text}' if ban_words_text else ''}
{'' if not patch_positive else '- 额外正面补丁: ' + patch_positive}
{'' if not patch_negative else '- 额外负面补丁(不要出现): ' + patch_negative}
请直接输出分镜提示词，控制在50-100字左右，用英文描述，不要解释。"""

        if fixed_prompt:
            pb = fixed_prompt
            logger.info(f"  Scene {sc.get('scene', '') or sc_title}: Using fixed prompt")
        else:
            try:
                content = deepseek_call(key, [{"role": "user", "content": prompt_req}], max_tokens=200)
                pb = content.strip()
                tracker.log("deepseek", f"Storyboard prompt: {sc_title[:10]}")
            except Exception as e:
                pb = f"{sc_title}, {sc_mood} atmosphere, {shot_type} shot, cinematic scene, dramatic lighting"
                tracker.log("deepseek", f"Storyboard prompt: {sc_title[:10]}", success=False)
                logger.warning(f"  Scene {sc.get('scene', '') or sc_title} DeepSeek failed: {e}")

        prompt_versions = _build_prompt_versions(
            pb,
            positive_patch=patch_positive,
            negative_patch=patch_negative,
            final_suffix=_style_text("storyboard_suffix"),
        )
        prompts.append(prompt_versions)
        logger.info(f"  Scene {sc.get('scene', '') or sc_title}: {pb[:50]}...")

    return prompts
def sanitize_storyboard_prompt(prompt):
    replacements = {
        "战袍": "战袍",
        "动甲": "战袍",
        "青袍": "青袍衣",
        "玄袍": "宗主袍",
        "黑袍": "黑袍",
        "白底": "白底红金袍",
        "素袍": "素袍",
        "青衣": "青袍衣",
        "清甲": "瘦长",
    }
    cleaned = prompt
    for src, dst in replacements.items():
        cleaned = cleaned.replace(src, dst)
    return cleaned
def _extract_mentioned_assets(text: str) -> list:
    """Scan text for mentions of known assets (characters/props/scenes) and return matches."""
    if not text:
        return []
    conn = get_db()
    assets = conn.execute(
        "SELECT id, name, type, filePath FROM t_assets "
        "WHERE type IN ('role','prop','scene') AND projectId=?",
        (PROJECT_ID,)
    ).fetchall()
    conn.close()

    seen = {}  # asset_id -> result dict

    # 1. Direct name match
    for aid, name, atype, fpath in assets:
        if name and name in text:
            seen[aid] = {"asset_id": aid, "name": name, "type": atype,
                         "has_image": bool(fpath), "matched_by": name}

    # 2. characters.json supporting_markers / props_list match
    for char_name, card in CHARACTER_IDENTITY_CARDS.items():
        char_asset = next(((a[0], a[2]) for a in assets if a[1] == char_name), None)
        if not char_asset:
            continue
        char_aid, char_fpath = char_asset

        all_markers = (
            card.get("supporting_markers", []) +
            card.get("props_list", []) +
            card.get("clothing_keywords", []) +
            card.get("face_keywords", [])
        )
        for marker in all_markers:
            if marker and len(marker) >= 2 and marker in text:
                if char_aid not in seen:
                    seen[char_aid] = {"asset_id": char_aid, "name": char_name,
                                      "type": "role", "has_image": bool(char_fpath),
                                      "matched_by": marker}
                # Also link props mentioned alongside this character
                for prop_name in card.get("props_list", []):
                    prop_asset = next(((a[0], a[2]) for a in assets
                                       if a[1] == prop_name and a[2] == "prop"), None)
                    if prop_asset and prop_asset[0] not in seen:
                        seen[prop_asset[0]] = {"asset_id": prop_asset[0], "name": prop_name,
                                               "type": "prop", "has_image": bool(prop_asset[1]),
                                               "matched_by": f"{marker}->{prop_name}"}

    return list(seen.values())
def _build_character_prompt(card):
    """Build a FLUX-compatible English prompt from a character identity card using LLM."""
    import hashlib as _hl

    # Build card text for LLM
    age = card.get("age", "")
    body = card.get("body", "")
    hair = card.get("hair", "")
    face = card.get("face", "")
    clothing = card.get("clothing_prompt_override") or card.get("clothing", "")
    age_group = card.get("age_group", "")
    gender_hint = "male" if any(k in body for k in ["男", "壮", "魁梧", "结实"]) else "female" if any(k in body for k in ["女", "纤", "苗条", "娇"]) else ""

    card_text = (
        f"角色年龄: {age}\n"
        f"体型: {body}\n"
        f"发型: {hair}\n"
        f"面部: {face}\n"
        f"服装: {clothing}\n"
    )

    cache_key = "char_" + _hl.md5(card_text.encode()).hexdigest()[:12]
    if cache_key in _translate_cache:
        return _translate_cache[cache_key]

    # Gender-specific rules
    male_rules = (
        "- For male characters: MUST include 'male, masculine face, light stubble, 5 o'clock shadow, strong brow ridge, thick eyebrows, angular jaw, no androgynous face, no feminine features'\n"
        "- For elderly males: add 'elderly old man, weathered face, deep wrinkles'\n"
        "- For chubby/round males: add 'heavy beard shadow, thick neck, broad nose, stout male body, masculine round face, not female round face'\n"
    )
    female_rules = "- For female characters: MUST include 'female, feminine, soft features, delicate face'\n"
    gender_rules = male_rules if gender_hint == "male" else female_rules if gender_hint == "female" else male_rules + female_rules

    try:
        key = get_deepseek_key()
        if key:
            result = deepseek_call(key, [
                {"role": "system", "content": (
                    "You are a FLUX model prompt engineer. Convert the Chinese character description below into a single English prompt for generating a character reference portrait.\n\n"
                    "STRICT RULES:\n"
                    "- Output ONLY the English prompt, nothing else. No explanations, no markdown.\n"
                    "- Start with: 'character reference sheet, single character portrait, dark atmospheric background'\n"
                    f"{gender_rules}"
                    "- CLOTHING DIFFERENTIATION: Describe fabric textures, layered clothing, wear/aging details, accessories in vivid English\n"
                    "- Translate all Chinese descriptions to specific, vivid English with rich visual detail\n"
                    "- End with: 'cinematic lighting, sharp focus, 8k uhd, no text, no watermark, no logo'\n"
                    "- Keep the prompt under 500 characters"
                )},
                {"role": "user", "content": card_text}
            ], max_tokens=200)
            if len(result) > 40:
                _translate_cache[cache_key] = result
                return result
    except Exception as e:
        logger.warning(f"  角色prompt LLM失败: {e}")

    # Fallback: basic English concatenation
    parts = ["character reference sheet, single character portrait, dark atmospheric background"]
    if gender_hint == "male":
        parts.append("male, masculine face, light stubble, strong brow ridge, angular jaw")
    elif gender_hint == "female":
        parts.append("female, feminine, soft features, delicate face")
    if age:
        parts.append(age)
    if hair:
        parts.append(hair)
    if clothing:
        parts.append(clothing[:80])
    parts.append("cinematic lighting, sharp focus, 8k uhd, no text, no watermark")
    return ", ".join(parts)


def _build_prop_prompt(card):
    # [docstring removed]
    import hashlib as _hl

    props_raw = card.get('props', '')
    if not props_raw:
        return "photorealistic prop design sheet, single object, atmospheric dark background with subtle lighting, cinematic lighting, 8k uhd, no human, no text, no watermark, no signature"

    # 构建 LLM 输入
    card_text = (
        f"道具: {props_raw}\n"
        f"角色年龄: {card.get('age', '')}\n"
        f"角色体型: {card.get('body', '')}\n"
    )

    # 缓存 key
    cache_key = "prop_" + _hl.md5(card_text.encode()).hexdigest()[:12]
    if cache_key in _translate_cache:
        return _translate_cache[cache_key]

    # 调用 LLM
    try:
        key = get_deepseek_key()
        if key:
            result = deepseek_call(key, [
                {"role": "system", "content": (
                    "You are a FLUX model prompt engineer. Convert the Chinese prop/weapon description below into a single English prompt for generating a photorealistic prop reference image.\n\n"
                    "STRICT RULES:\n"
                    "- Output ONLY the English prompt, nothing else. No explanations, no markdown.\n"
                    "- Start with: 'photorealistic prop design sheet, single object centered, atmospheric dark background with subtle lighting'\n"
                    "- Translate the prop name and description to vivid English with SPECIFIC material, texture, color, and mystical details\n"
                    "- MUST add at least 3 of these material/texture tags: oxidized bronze, tarnished patina, verdigris, carved runes, dark wood grain, cracked jade, blood-red inlay, rusted iron, worn leather, ancient script engraving\n"
                    "- MUST add atmosphere: 'ancient Chinese dark fantasy artifact, occult atmosphere, eerie glow, centuries-old wear'\n"
                    "- MUST add: 'museum-quality specimen photo, dramatic side lighting revealing texture'\n"
                    "- NEVER describe as modern, clean, shiny, or new - everything must look ANCIENT and WEATHERED\n"
                    "- End with: 'cinematic lighting, sharp focus, 8k uhd, no human, no hands, no face, no text, no watermark, no logo'\n"
                    "- Keep the prompt under 500 characters"
                )},
                {"role": "user", "content": card_text}
            ], max_tokens=200)
            if len(result) > 40:
                _translate_cache[cache_key] = result
                return result
    except Exception as e:
        logger.warning(f"  道具prompt LLM失败: {e}")

    # Fallback: 通用描述
    return (
        f"photorealistic prop design sheet, single object centered, atmospheric dark background with subtle lighting, "
        f"ancient Chinese dark fantasy artifact, oxidized bronze, tarnished patina, carved runes, "
        f"occult atmosphere, eerie glow, centuries-old wear, {props_raw}, "
        f"museum-quality specimen photo, dramatic side lighting, "
        f"cinematic lighting, sharp focus, 8k uhd, "
        f"no human, no hands, no face, no text, no watermark, no logo"
    )


def sync_character_assets_to_toonflow(s_http, regen_all=False):
    # [docstring removed]

    if not CHARACTER_IDENTITY_CARDS:
        return

    # 读取已有角色资产 (name ?asset_id)
    existing = {}
    try:
        r = s_http.post(BASE + "/assets/getAssets", json={"projectId": PROJECT_ID, "type": "角色"}, timeout=10)
        if r.status_code == 200:
            data = r.json().get("data", [])
            if isinstance(data, list):
                existing = {a.get("name", ""): a.get("id") for a in data if a.get("name")}
    except Exception:
        pass
    if not existing:
        conn = get_db()
        rows = conn.execute(
            "SELECT name, id FROM t_assets WHERE type='角色' AND projectId=?", (PROJECT_ID,)
        ).fetchall()
        conn.close()
        existing = {r[0]: r[1] for r in rows}

    raw_chars = _load_characters_raw()

    # new_chars: characters in identity cards but not yet in DB
    new_chars = [(n, raw_chars.get(n, c)) for n, c in CHARACTER_IDENTITY_CARDS.items() if n not in existing]

    # regen_all: 已有角色也重新生成图?
    regen_chars = []
    if regen_all:
        regen_chars = [(n, raw_chars.get(n, c), existing[n]) for n, c in CHARACTER_IDENTITY_CARDS.items() if n in existing]
    regen_names = {n for n, _, _ in regen_chars}
    reuse_chars = [(n, raw_chars.get(n, c), existing[n]) for n, c in CHARACTER_IDENTITY_CARDS.items() if n in existing and n not in regen_names]

    for name, card, aid in reuse_chars:
        prompt_versions = _build_prompt_versions(
            _build_character_prompt(card),
            required_terms=["cinematic lighting", "no text", "no watermark"],
        )
        _upsert_prompt_trace("character_assets", name, {
            "asset_id": aid,
            "name": name,
            "raw": prompt_versions["raw"],
            "polished": prompt_versions["polished"],
            "final": prompt_versions["final"],
            "action": "reuse",
        })

    if not new_chars and not regen_chars:
        return

    if new_chars:
        logger.info(f"  新增 {len(new_chars)} 个角色: {[n for n,_ in new_chars]}")
    if regen_chars:
        logger.info(f"  重新生成 {len(regen_chars)} 个角色参考图: {[n for n,_,_ in regen_chars]}")

    conn = get_db()
    new_with_ids = []
    for name, card in new_chars:
        intro = ""
        prompt_versions = _build_prompt_versions(
            _build_character_prompt(card),
            required_terms=["cinematic lighting", "no text", "no watermark"],
        )
        prompt_en = prompt_versions["final"]
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO t_assets (name, type, intro, videoPrompt, projectId, prompt) VALUES (?,?,?,?,?,?)",
            (name, "角色", intro[:200], prompt_en[:500], PROJECT_ID, prompt_en[:500])
        )
        asset_id = cur.lastrowid
        new_with_ids.append((asset_id, prompt_en, name))
        # 记录 prompt trace
        _upsert_prompt_trace("character_assets", name, {
            "asset_id": asset_id,
            "name": name,
            "raw": prompt_versions["raw"],
            "polished": prompt_versions["polished"],
            "final": prompt_en,
            "action": "insert",
        })
    conn.commit()
    conn.close()

    all_gen = []
    for aid, prompt_en, name in new_with_ids:
        all_gen.append((aid, prompt_en, name))
    for name, card, aid in regen_chars:
        prompt_versions = _build_prompt_versions(
            _build_character_prompt(card),
            required_terms=["cinematic lighting", "no text", "no watermark"],
        )
        prompt_en = prompt_versions["final"]
        conn = get_db()
        conn.execute("UPDATE t_assets SET prompt=?, videoPrompt=? WHERE id=?",
                    (prompt_en[:500], prompt_en[:500], aid))
        conn.commit()
        conn.close()
        # 记录 prompt trace (regen)
        _upsert_prompt_trace("character_assets", name, {
            "asset_id": aid,
            "name": name,
            "raw": prompt_versions["raw"],
            "polished": prompt_versions["polished"],
            "final": prompt_en,
            "action": "regen",
        })
        all_gen.append((aid, prompt_en, name))

    ok = 0
    for aid, prompt_en, name in all_gen:
        if _generate_and_store_asset_image(aid, "角色", prompt_en):
            ok += 1
        time.sleep(2)


def _get_outline_scenes(outline_id=None):
    # [docstring removed]
    try:
        conn = get_db()
        if outline_id:
            rows = conn.execute(
                "SELECT data FROM t_outline WHERE id=?", (outline_id,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT data FROM t_outline WHERE projectId=?", (PROJECT_ID,)
            ).fetchall()
        conn.close()
        all_scenes = []
        seen_names = set()
        for row in rows:
            data = json.loads(row[0] or "{}")
            for sc in data.get("scenes", []):
                name = sc.get("name", "").strip()
                if name and name not in seen_names:
                    seen_names.add(name)
                    all_scenes.append(sc)
        return all_scenes
    except Exception as e:
        logger.warning(f"  读取 outline.scenes 失败: {e}")
        return []


def _is_reusable_scene_space(name, explicit_location=""):
    value = (name or "").strip()
    if not value:
        return False
    reject_exact = {
    }
    reject_tokens = [
    ]
    allow_tokens = [
    ]
    if value in reject_exact:
        return False
        return False
    if any(token in value for token in allow_tokens):
        return True
    return bool(explicit_location) and len(value) >= 3 and value not in reject_exact


def _extract_location_name(scene_title, scene_desc):
    # [docstring removed]
    _SPACE_KEYWORDS = [
    ]
    for kw in _SPACE_KEYWORDS:
        if kw in scene_title:
            return scene_title
    # title 不含空间词,从描述中提取
    text = (scene_desc or "") + (scene_title or "")
    for kw in _SPACE_KEYWORDS:
        if kw in text:
            idx = text.index(kw)
            start = max(0, idx - 1)
            raw = text[start:idx + len(kw)]
            cleaned = re.sub(r'^[满遍过入出上下里外中内]', '', cleaned)
            return cleaned if len(cleaned) >= 2 else raw
    return None


def _resolve_scene_asset_name(scene_title, scene_desc, explicit_location=""):
    explicit_location = (explicit_location or "").strip()
    if explicit_location:
        return explicit_location if _is_reusable_scene_space(explicit_location, explicit_location) else None
    extracted = _extract_location_name(scene_title or "", scene_desc or "")
    if not extracted:
        return None
    extracted = extracted.strip()
    if len(extracted) < 2:
        return None
    return extracted if _is_reusable_scene_space(extracted) else None


def _collect_scene_asset_candidates(scenes):
    candidates = []
    seen_names = set()
    for sc in scenes or []:
        raw_title = (sc.get("title") or sc.get("name") or "").strip()
        raw_desc = (sc.get("desc") or sc.get("description") or sc.get("content") or "").strip()
        raw_location = (sc.get("location") or "").strip()
        name = _resolve_scene_asset_name(raw_title, raw_desc, explicit_location=raw_location)
        if not name or name in seen_names:
            continue
        seen_names.add(name)
        candidates.append({
            "name": name,
            "description": _clean_scene_description_for_env(raw_desc, name),
            "source_title": raw_title,
            "source_location": raw_location,
        })
    return candidates


def sync_scene_assets_to_toonflow(scenes, auto_gen_images: bool = True, regen_all: bool = False, outline_id=None):
    # [docstring removed]
    # [docstring removed]
    if not scenes:
        return
    logger.info(f"Step 5.5b: 同场景资产 ({len(scenes)} ? auto_gen={auto_gen_images}, regen={regen_all})")
    conn = get_db()
    existing = {r[0]: r[1] for r in conn.execute(
        "SELECT name, filePath FROM t_assets WHERE type='场景' AND projectId=?", (PROJECT_ID,)
    ).fetchall()}
    new_ids = []
    regen_ids = []
    added = 0
    scene_candidates = _collect_scene_asset_candidates(scenes)
    if scene_candidates:
        pass  # [encoding fixed]
    else:
        outline_scenes = _get_outline_scenes(outline_id)
        scene_candidates = _collect_scene_asset_candidates(outline_scenes)

    if not scene_candidates:
        conn.close()
        logger.warning("  ️ 會识别到真实物理间,跳过场景资产同")
        return

    for item in scene_candidates:
        name = item["name"]
        desc_clean = item["description"]
        prompt_versions = _build_prompt_versions(
            _asset_style_prompt("scene", name=name, desc=desc_clean),
            required_terms=["cinematic lighting", "no text", "no watermark"],
        )
        prompt = prompt_versions["final"]
        if name in existing:
            if regen_all:
                row = conn.execute(
                    "SELECT id FROM t_assets WHERE name=? AND type='场景' AND projectId=?",
                    (name, PROJECT_ID)
                ).fetchone()
                if row:
                    regen_ids.append((row[0], prompt))
            elif auto_gen_images and not existing[name]:
                row = conn.execute(
                    "SELECT id FROM t_assets WHERE name=? AND type='场景' AND projectId=?",
                    (name, PROJECT_ID)
                ).fetchone()
                if row:
                    new_ids.append((row[0], prompt))
            else:
                row = conn.execute(
                    "SELECT id FROM t_assets WHERE name=? AND type='场景' AND projectId=?",
                    (name, PROJECT_ID)
                ).fetchone()
                if row:
                    _upsert_prompt_trace("scene_assets", name, {
                        "asset_id": row[0],
                        "name": name,
                        "description": desc_clean,
                        "raw": prompt_versions["raw"],
                        "polished": prompt_versions["polished"],
                        "final": prompt,
                        "action": "reuse",
                    })
            continue
        intro = f"{desc_clean[:200]}"
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO t_assets (name, type, intro, prompt, videoPrompt, projectId) VALUES (?,?,?,?,?,?)",
            (name, "场景", intro, prompt[:500], prompt[:500], PROJECT_ID)
        )
        asset_id = cur.lastrowid
        new_ids.append((asset_id, prompt))
        added += 1
        _upsert_prompt_trace("scene_assets", name, {
            "asset_id": asset_id,
            "name": name,
            "description": desc_clean,
            "raw": prompt_versions["raw"],
            "polished": prompt_versions["polished"],
            "final": prompt,
            "action": "insert",
        })
    conn.commit()
    conn.close()

    if regen_ids:
        conn = get_db()
        for aid, pmt in regen_ids:
            prompt_versions = _build_prompt_versions(
                pmt,
                required_terms=["cinematic lighting", "no text", "no watermark"],
            )
            pmt_final = prompt_versions["final"]
            conn.execute("UPDATE t_assets SET prompt=?, videoPrompt=? WHERE id=?",
                        (pmt_final[:500], pmt_final[:500], aid))
            # 记录 prompt trace (regen)
            row = conn.execute("SELECT name FROM t_assets WHERE id=?", (aid,)).fetchone()
            if row:
                name = row[0]
                _upsert_prompt_trace("scene_assets", name, {
                    "asset_id": aid,
                    "name": name,
                    "raw": prompt_versions["raw"],
                    "polished": prompt_versions["polished"],
                    "final": pmt_final,
                    "action": "regen",
                })
        conn.commit()
        conn.close()
        logger.info(f"  重新生成 {len(regen_ids)} 东晏考图: {[n for _,n in regen_ids]}")

    # 生成图片
    all_gen = [(aid, pmt) for aid, pmt in new_ids]
    all_gen += [(aid, pmt) for aid, pmt in regen_ids]
    if auto_gen_images and all_gen:
        logger.info(f"  生成 {len(all_gen)} 张场晃晛 (ComfyUI)...")
        ok = 0
        for aid, pmt in all_gen:
            if _generate_and_store_asset_image(aid, "场景", pmt):
                ok += 1
            time.sleep(2)


def sync_prop_assets_to_toonflow(auto_gen_images: bool = True, regen_all: bool = False):
    # [docstring removed]
    # [docstring removed]
    if not CHARACTER_IDENTITY_CARDS:
        return
    logger.info(f"Step 5.5c: 同道具资产 (auto_gen={auto_gen_images}, regen={regen_all})")
    raw_chars = _load_characters_raw()
    conn = get_db()
    existing = {r[0]: r[1] for r in conn.execute(
        "SELECT name, id FROM t_assets WHERE type='道具' AND projectId=?", (PROJECT_ID,)
    ).fetchall()}
    new_ids = []
    regen_ids = []
    added = 0
    for char_name, card in CHARACTER_IDENTITY_CARDS.items():
        props_desc = card.get("props", "")
        if not props_desc:
            continue
        prop_name = props_desc.split(", ")[0].split("/")[0].strip()
        raw_card = raw_chars.get(char_name, card)
        prompt_versions = _build_prompt_versions(
            _build_prop_prompt(raw_card),
            required_terms=["cinematic lighting", "no text", "no watermark"],
        )
        prompt = prompt_versions["final"]
        if prop_name in existing:
            if regen_all:
                # 强制重新生成
                regen_ids.append((existing[prop_name], prompt, prop_name))
            elif auto_gen_images and not existing[prop_name]:
                row = conn.execute(
                    "SELECT id FROM t_assets WHERE name=? AND type='道具' AND projectId=?",
                    (prop_name, PROJECT_ID)
                ).fetchone()
                if row:
                    new_ids.append((row[0], prompt))
            else:
                _upsert_prompt_trace("prop_assets", prop_name, {
                    "asset_id": existing[prop_name],
                    "name": prop_name,
                    "character_name": char_name,
                    "raw": prompt_versions["raw"],
                    "polished": prompt_versions["polished"],
                    "final": prompt,
                    "action": "reuse",
                })
            continue
        intro = ""
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO t_assets (name, type, intro, prompt, videoPrompt, projectId) VALUES (?,?,?,?,?,?)",
            (prop_name, "道具", intro[:200], prompt[:500], prompt[:500], PROJECT_ID)
        )
        asset_id = cur.lastrowid
        new_ids.append((asset_id, prompt))
        added += 1
        # 记录 prompt trace
        _upsert_prompt_trace("prop_assets", prop_name, {
            "asset_id": asset_id,
            "name": prop_name,
            "character_name": char_name,
            "raw": prompt_versions["raw"],
            "polished": prompt_versions["polished"],
            "final": prompt,
            "action": "insert",
        })
    conn.commit()
    conn.close()

    # 更新 regen 道具?prompt
    if regen_ids:
        conn = get_db()
        for aid, pmt, pname in regen_ids:
            prompt_versions = _build_prompt_versions(
                pmt,
                required_terms=["cinematic lighting", "no text", "no watermark"],
            )
            pmt_final = prompt_versions["final"]
            conn.execute("UPDATE t_assets SET prompt=?, videoPrompt=? WHERE id=?",
                        (pmt_final[:500], pmt_final[:500], aid))
            # 记录 prompt trace (regen)
            _upsert_prompt_trace("prop_assets", pname, {
                "asset_id": aid,
                "name": pname,
                "raw": prompt_versions["raw"],
                "polished": prompt_versions["polished"],
                "final": pmt_final,
                "action": "regen",
            })
        conn.commit()
        conn.close()
        logger.info(f"  重新生成 {len(regen_ids)} 丁具参考图: {[n for _,_,n in regen_ids]}")

    # 生成图片
    all_gen = [(aid, pmt) for aid, pmt in new_ids]
    all_gen += [(aid, pmt) for aid, pmt, _ in regen_ids]
    if auto_gen_images and all_gen:
        logger.info(f"  生成 {len(all_gen)} 张道具图?(ComfyUI FLUX)...")
        ok = 0
        for aid, pmt in all_gen:
            if _generate_and_store_asset_image(aid, "道具", pmt):
                ok += 1
            time.sleep(2)


def audit_assets(auto_clean: bool = False):
    # [docstring removed]
    # [syntax fixed]
    # [docstring removed]
    logger.info("Step 5.6: 资产実")
    conn = get_db()
    all_assets = conn.execute(
        "SELECT id, name, type, intro, filePath FROM t_assets WHERE projectId=? ORDER BY type, id",
        (PROJECT_ID,)
    ).fetchall()

    issues = []  # (id, name, type, issue_desc)

    suspect_keywords = ['叶辰', '', '玄尘', '林动', '萂', '唐三']
    for r in all_assets:
        intro = r[3] or ''
        for kw in suspect_keywords:
            if kw in intro or kw in r[1]:
                issues.append((r[0], r[1], r[2], f"非本项目角色(?{kw}')"))
                break

    # 2. 无图片资?
    for r in all_assets:
        if not r[4]:
            pass  # [encoding fixed]
    # 3. 图片文件不存?
    upload_dir = os.path.join(os.environ.get('APPDATA', ''), 'toonflow-app', 'uploads')
    for r in all_assets:
        if r[4]:
            fpath = os.path.join(upload_dir, r[4].lstrip("/"))
            if not os.path.exists(fpath):
                issues.append((r[0], r[1], r[2], f"图片文件不存?{r[4]})"))

    name_count = {}
    for r in all_assets:
        key = (r[2], r[1])  # (type, name)
        name_count[key] = name_count.get(key, 0) + 1
    for (typ, name), cnt in name_count.items():
        if cnt > 1:
            ids = [r[0] for r in all_assets if r[2] == typ and r[1] == name]

    if not issues:
        logger.info("  ?资产実通过,无")
        conn.close()
        return

    logger.info(f"  ️ 发现 {len(issues)} 丗?")
    for aid, name, typ, desc in issues:
        logger.info(f"    id={aid} [{typ}] {name}: {desc}")

    if auto_clean:
        del_ids = set()
        for aid, name, typ, desc in issues:
                del_ids.add(aid)

        if del_ids:
            ids_str = ','.join(str(i) for i in del_ids)
            conn.execute(f"DELETE FROM t_assets WHERE id IN ({ids_str})")
            conn.commit()
        else:
            logger.info("  无可臊清理的资?重名手动处理)")

    conn.close()


def _generate_and_store_asset_image(asset_id: int, asset_type: str, prompt: str) -> bool:
    import uuid
    type_dir_map = {"角色": "role", "场景": "scene", "道具": "prop"}
    sub_dir = type_dir_map.get(asset_type, "other")
    toonflow_data = os.path.join(os.environ["APPDATA"], "toonflow-app")

    try:
        neg = VISUAL_NEGATIVE
        if asset_type == "场景":
            neg += ", person, people, human, man, woman, boy, girl, face, body, figure, character, portrait, hands, fingers, skin, hair, clothing on person, 1boy, 1girl"
        profile = get_style_profile(PROJECT_ID)
        if profile.get("image_engine") == "klein":
            img_bytes = _generate_klein_image(
                prompt, neg_prompt=neg, profile=profile,
            )
        else:
            img_bytes = _generate_image_fallback(prompt, neg_prompt=neg)
        if not img_bytes:
            return False
        img_bytes = _remove_watermark_corner(img_bytes)
        fname = f"{uuid.uuid4()}.jpg"
        full_dir = os.path.join(toonflow_data, "uploads", str(PROJECT_ID), sub_dir)
        os.makedirs(full_dir, exist_ok=True)
        with open(os.path.join(full_dir, fname), "wb") as f:
            f.write(img_bytes)
        rel_path = f"/{PROJECT_ID}/{sub_dir}/{fname}"
        conn = get_db()
        conn.execute("UPDATE t_assets SET filePath=? WHERE id=?", (rel_path, asset_id))
        conn.commit()
        conn.close()
        logger.info(f"  ?[{asset_type}] asset_id={asset_id} ?{rel_path} ({len(img_bytes)//1024}KB)")
        return True
    except Exception as e:
        logger.warning(f"  ️ [{asset_type}] asset_id={asset_id} 图片生成失败: {e}")
        return False


def _load_char_refs(s_http):
    # [docstring removed]
    import base64
    chars = []
    ref_map = {}
    # 优先 API
    try:
        r = s_http.post(BASE + "/assets/getAssets", json={"projectId": PROJECT_ID, "type": "角色"}, timeout=10)
        if r.status_code == 200:
            data = r.json().get("data", [])
            if isinstance(data, list) and data:
                chars = [(a.get("id"), a.get("name",""), a.get("intro",""), a.get("filePath","")) for a in data if a.get("filePath")]
    except Exception:
        pass
    if not chars:
        conn = get_db()
        chars = conn.execute(
            "SELECT id, name, intro, filePath FROM t_assets WHERE type='角色' AND filePath IS NOT NULL AND projectId=?",
            (PROJECT_ID,)
        ).fetchall()
        conn.close()
    uploads_dir = os.path.join(os.environ.get("APPDATA", ""), "toonflow-app", "uploads")
    for cid, name, intro, fpath in chars:
        loaded = False
        local_path = os.path.join(uploads_dir, fpath.lstrip("/"))
        if os.path.isfile(local_path):
                try:
                    with open(local_path, "rb") as f:
                        img_bytes = f.read()
                    b64 = base64.b64encode(img_bytes).decode()
                    ext = fpath.rsplit(".", 1)[-1] if "." in fpath else "jpg"
                    ref_map[name] = {
                        "b64": f"data:image/{ext};base64,{b64}",
                        "intro": (intro or "")[:60],
                        "id": cid,
                    }
                    loaded = True
                except Exception as e:
                    logger.warning(f"  角色参图朜读取失败 {name}: {e}")
        if not loaded:
            try:
                if fpath and fpath.startswith("http"):
                    img_url = fpath.replace("127.0.0.1:60000", "localhost:60000")
                else:
                    img_url = BASE + fpath
                r = s_http.get(img_url, timeout=10)
                if r.status_code == 200:
                    b64 = base64.b64encode(r.content).decode()
                    ext = fpath.rsplit(".", 1)[-1] if "." in fpath else "jpg"
                    ref_map[name] = {
                        "b64": f"data:image/{ext};base64,{b64}",
                        "intro": (intro or "")[:60],
                        "id": cid,
                    }
                    logger.info(f"  角色参图: {name} ({len(r.content)//1024}KB)")
            except Exception as e:
                logger.warning(f"  角色参图加载失败 {name}: {e}")
    return ref_map


def _load_scene_refs(s_http):
    # [docstring removed]
    import base64
    scenes = []
    # 优先 API
    try:
        r = s_http.post(BASE + "/assets/getAssets", json={"projectId": PROJECT_ID, "type": "场景"}, timeout=10)
        if r.status_code == 200:
            data = r.json().get("data", [])
            if isinstance(data, list) and data:
                scenes = [(a.get("id"), a.get("name", ""), a.get("intro", ""), a.get("filePath", "")) for a in data if a.get("filePath")]
    except Exception:
        pass
    # DB 兜底
    if not scenes:
        conn = get_db()
        scenes = conn.execute(
            "SELECT id, name, intro, filePath FROM t_assets WHERE type='场景' AND filePath IS NOT NULL AND projectId=?",
            (PROJECT_ID,)
        ).fetchall()
        conn.close()
    if not scenes:
        logger.info("  ️ 当前项目无场晏考图")
        return {}
    ref_map = {}  # name -> {"b64": data_uri, "intro": intro_text, "id": asset_id}
    uploads_dir = os.path.join(os.environ.get("APPDATA", ""), "toonflow-app", "uploads")
    for sid, name, intro, fpath in scenes:
        loaded = False
        if fpath and not fpath.startswith("http"):
            local_path = os.path.join(uploads_dir, fpath.lstrip("/"))
            if os.path.isfile(local_path):
                try:
                    with open(local_path, "rb") as f:
                        img_bytes = f.read()
                    b64 = base64.b64encode(img_bytes).decode()
                    ext = fpath.rsplit(".", 1)[-1] if "." in fpath else "jpg"
                    ref_map[name] = {
                        "b64": f"data:image/{ext};base64,{b64}",
                        "intro": (intro or "")[:80],
                        "id": sid,
                    }
                    loaded = True
                except Exception as e:
                    logger.warning(f"  场景参图朜读取失败 {name}: {e}")
        if not loaded:
            try:
                if fpath and fpath.startswith("http"):
                    img_url = fpath.replace("127.0.0.1:60000", "localhost:60000")
                else:
                    img_url = BASE + fpath
                r = s_http.get(img_url, timeout=10)
                if r.status_code == 200:
                    b64 = base64.b64encode(r.content).decode()
                    ext = fpath.rsplit(".", 1)[-1] if "." in fpath else "jpg"
                    ref_map[name] = {
                        "b64": f"data:image/{ext};base64,{b64}",
                        "intro": (intro or "")[:80],
                        "id": sid,
                    }
                    logger.info(f"  场景参图: {name} ({len(r.content)//1024}KB)")
            except Exception as e:
                logger.warning(f"  场景参图加载失败 {name}: {e}")
    return ref_map


def _match_scene_ref(prompt_text, scene_refs):
    # [docstring removed]
    if not scene_refs:
        return None
    prompt_lower = prompt_text.lower()
    best_match = None
    best_score = 0
    for name, info in scene_refs.items():
        score = 0
        import re
        # 拆出2-4字中文词
        name_tokens = re.findall(r'[\u4e00-\u9fff]{2,4}', name)
        for tok in name_tokens:
            if tok in prompt_lower:
                score += len(tok)
        intro = info.get("intro", "") if isinstance(info, dict) else ""
        intro_tokens = re.findall(r'[\u4e00-\u9fff]{2,4}', intro)
        for tok in intro_tokens:
            if tok in prompt_lower:
                score += 1
        if score > best_score:
            best_score = score
            best_match = name
    if best_score >= 2:
        return best_match
    return None


def _match_char(prompt_text, char_refs):
    # [docstring removed]
    raw_chars = _load_characters_raw()
    for name, info in char_refs.items():
        intro = info.get("intro", "") if isinstance(info, dict) else ""
        colors = re.findall(r'[白红金紫青蓝黑银灰绿深浅]+?', intro)
        for c in colors:
            if c in prompt_text:
                return name
        card = raw_chars.get(name, CHARACTER_IDENTITY_CARDS.get(name, {}))
        kw_fields = [card.get("clothing", ""), card.get("hair", ""), card.get("body", ""), card.get("props", "")]
        kw_text = "".join(kw_fields)
        kw_tokens = re.findall(r'[\u4e00-\u9fff]{2,4}', kw_text)
        if any(tok in prompt_text for tok in kw_tokens if len(tok) >= 2):
            return name
    return _default_protagonist() if _default_protagonist() in char_refs else (list(char_refs.keys())[0] if char_refs else None)


def _match_roles(prompt_text, char_refs, max_roles=2):
    matched = []
    for name, info in char_refs.items():
        intro = info["intro"]
        colors = re.findall(r'[白红金紫青蓝黑银灰绿深浅]+?', intro)
        if name in prompt_text or any(c and c in prompt_text for c in colors):
            matched.append(name)
    priority = list(CHARACTER_IDENTITY_CARDS.keys()) if CHARACTER_IDENTITY_CARDS else list(char_refs.keys())
    ordered = []
    for name in priority:
        if name in matched and name not in ordered:
            ordered.append(name)
    for name in matched:
        if name not in ordered:
            ordered.append(name)
    if not ordered:
        main_char = _match_char(prompt_text, char_refs)
        ordered = [main_char] if main_char else []
    return [name for name in ordered[:max_roles] if name in char_refs]


def _normalize_scene_roles(scene_roles, char_refs, max_roles=2):
    if not scene_roles:
        return []
    normalized = []
    for role in scene_roles:
        if isinstance(role, dict):
            role = role.get("name")
        role = (role or "").strip()
        if role in char_refs and role not in normalized:
            normalized.append(role)
    return normalized[:max_roles]


def _save_candidate_image(content, candidate_dir, asset_id, candidate_index):
    import uuid
    os.makedirs(candidate_dir, exist_ok=True)
    fname = f"asset_{asset_id}_cand_{candidate_index}_{uuid.uuid4().hex[:8]}.jpg"
    full_path = os.path.join(candidate_dir, fname)
    with open(full_path, "wb") as f:
        f.write(content)
    return full_path


def _score_image_bytes(img_bytes, prompt=""):
    # [docstring removed]
    # [docstring removed]
    import tempfile, io
    score = 0
    issues = []
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(img_bytes))
        # 临时保存到文件以复用check_file
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tf:
            img.save(tf, format="JPEG", quality=95)
            tf_path = tf.name
        score, issues = _candidate_score(tf_path, prompt=prompt)
        try:
            os.unlink(tf_path)
        except OSError:
            pass

        try:
            from image_backends import nvidia_vision_audit
            import base64
            img_b64 = base64.b64encode(img_bytes).decode()
            nv_issues = nvidia_vision_audit(img_b64, prompt=prompt)
            if nv_issues:
                # Map English tags to Chinese + scoring
                tag_map = {
                    "subject_truncated": ("主体疑似裁切", 95),
                    "unwanted_text": ("疑似文字/乱码", 50),
                    "background_plain": ("背景过白/过黑", 10),
                    "dull_colors": ("色彩平淡饱和", 40),
                    # [orphan dict fixed]
                }
                for iss in nv_issues:
                    cn_iss, add_score = tag_map.get(iss, (iss, 30))
                    if cn_iss not in issues:
                        issues.append(cn_iss)
                        score += add_score
        # [except removed]
        # [except removed]
            logger.debug(f"  Nvidia VL宠跳过: {e_nv}")

    # [except removed]
    # [pass removed]
    # [return moved]
    # [logger removed]
    # [return removed]


        except Exception:
            pass
            return score, issues

    except Exception:
        pass

    for issue in issues:
        if "疑似水印" in issue:
            score += 70
        elif "背景过白" in issue or "背景过黑" in issue:
            score += 50
            score += 40
            score += 30
            score += 60
            score += 55
            score += 45
        elif "疑似文字/乱码" in issue:
            score += 50
        elif "主体疑似裁切" in issue:
            score += 95
            if is_long_shot:
                score += 70
        elif "歙数量异常" in issue:
            score += 70
        elif "构图贴边风险" in issue:
            score += 55
            if is_long_shot:
                score += 30
        elif "核心道具缺" in issue:
            score += 75
        elif "镜类型不" in issue:
            score += 90
            if is_long_shot:
                score += 120
    try:
        _lp = locals().get("tf_path") or locals().get("local_path")
        if _lp:
            size_kb = os.path.getsize(_lp) / 1024
            if size_kb < 150:
                score += int(150 - size_kb)
    except Exception:
        pass
    return score, issues


def _phash(image_bytes, hash_size=8):
    # [docstring removed]
    try:
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(image_bytes)).convert("L").resize((hash_size * 4, hash_size * 4))
        import numpy as _np
        arr = _np.array(img, dtype=_np.float64)
        # 2D DCT-II using pure NumPy (no scipy dependency)
        M, N = arr.shape
        # DCT along columns (axis=0)
        dct_v = _np.zeros_like(arr)
        for k in range(M):
            n = _np.arange(M)
            dct_v[k, :] = _np.sum(arr * _np.cos(_np.pi * (2 * n + 1) * k / (2 * M)), axis=0)
        # DCT along rows (axis=1)
        dct = _np.zeros_like(arr)
        for k in range(N):
            n = _np.arange(N)
            dct[:, k] = _np.sum(dct_v * _np.cos(_np.pi * (2 * n + 1) * k / (2 * N)), axis=1)
        # Take the top-left hash_size x hash_size low-frequency block
        dct_low = dct[:hash_size, :hash_size]
        # Flatten to get row_mean (64 values for hash_size=8)
        row_mean = dct_low.flatten()
        median_val = _np.median(row_mean)
        return int("".join("1" if v > median_val else "0" for v in row_mean), 2)
    except Exception:
        return 0


def _hamming_distance(h1, h2):
    return bin(h1 ^ h2).count("1")


def _norm_text(text):
    return " ".join((text or "").replace("\n", " ").replace("\r", " ").split())


def _text_similarity(a, b):
    return SequenceMatcher(None, _norm_text(a), _norm_text(b)).ratio()


def _choose_storyboard_keep(a, b):
    score_a = 0.0
    score_b = 0.0
    if a.get("exists"):
        score_a += 3
    if b.get("exists"):
        score_b += 3
    score_a += min(len(_norm_text(a.get("prompt") or "")), 400) / 1000
    score_b += min(len(_norm_text(b.get("prompt") or "")), 400) / 1000
    score_a += (a.get("size_bytes") or 0) / 1024 / 1024
    score_b += (b.get("size_bytes") or 0) / 1024 / 1024
    if score_a > score_b:
        return a, b
    if score_b > score_a:
        return b, a
    if (a.get("shotIndex") or 0) <= (b.get("shotIndex") or 0):
        return a, b
    return b, a


def _auto_dedupe_storyboards(script_id, shot_gap=2, max_hamming=9, min_text_ratio=0.55):
    conn = get_db()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, projectId, scriptId, shotIndex, name, prompt, filePath "
        "FROM t_assets WHERE projectId=? AND scriptId=? AND type='分镜' AND filePath IS NOT NULL "
        "ORDER BY shotIndex, id",
        (PROJECT_ID, script_id),
    ).fetchall()
    uploads_dir = os.path.join(os.environ["APPDATA"], "toonflow-app", "uploads")
    items = []
    for row in rows:
        item = dict(row)
        abs_path = os.path.join(uploads_dir, (item.get("filePath") or "").lstrip("/").replace("/", os.sep))
        item["abs_path"] = abs_path
        item["exists"] = os.path.exists(abs_path)
        item["size_bytes"] = os.path.getsize(abs_path) if item["exists"] else 0
        item["hash"] = 0
        if item["exists"]:
            try:
                with open(abs_path, "rb") as f:
                    item["hash"] = _phash(f.read())
            except Exception:
                item["exists"] = False
                item["size_bytes"] = 0
                item["hash"] = 0
        items.append(item)

    delete_ids = set()
    deleted_shots = []
    groups = []
    visited = set()
    for i, seed in enumerate(items):
        if seed["id"] in visited or not seed.get("hash"):
            continue
        group = [seed]
        for cand in items[i + 1:]:
            if cand["id"] in visited or not cand.get("hash"):
                continue
            if seed.get("scriptId") != cand.get("scriptId"):
                continue
            seed_shot = seed.get("shotIndex")
            cand_shot = cand.get("shotIndex")
            if seed_shot is not None and cand_shot is not None and abs(int(seed_shot) - int(cand_shot)) > shot_gap:
                continue
            dist = _hamming_distance(seed["hash"], cand["hash"])
            txt = _text_similarity(seed.get("prompt") or "", cand.get("prompt") or "")
            near_name = _text_similarity(seed.get("name") or "", cand.get("name") or "")
            if dist <= max_hamming and (txt >= min_text_ratio or near_name >= 0.72):
                group.append(cand)
        if len(group) > 1:
            for item in group:
                visited.add(item["id"])
            keep = group[0]
            drops = []
            for item in group[1:]:
                keep, drop = _choose_storyboard_keep(keep, item)
                drops.append(drop)
            groups.append({
                "keep_id": keep["id"],
                "shot_indexes": [g.get("shotIndex") for g in group],
                "drop_ids": [d["id"] for d in drops],
            })
            for drop in drops:
                delete_ids.add(drop["id"])
                if drop.get("shotIndex") is not None:
                    deleted_shots.append(str(drop["shotIndex"]))

    if delete_ids:
        delete_list = sorted(delete_ids)
        rows_to_delete = conn.execute(
            f"SELECT id, filePath FROM t_assets WHERE id IN ({','.join('?' for _ in delete_list)})",
            delete_list,
        ).fetchall()
        conn.execute(
            f"DELETE FROM t_assets WHERE id IN ({','.join('?' for _ in delete_list)})",
            delete_list,
        )
        conn.commit()
        deleted_files = 0
        for _, file_path in rows_to_delete:
            abs_path = os.path.join(uploads_dir, (file_path or "").lstrip("/").replace("/", os.sep))
            if os.path.exists(abs_path):
                try:
                    os.remove(abs_path)
                    deleted_files += 1
                except OSError:
                    pass
        for idx, group in enumerate(groups, start=1):
            logger.info(f"    ȥ{idx}: shots={group['shot_indexes']} keep={group['keep_id']} drop={group['drop_ids']}")
    conn.close()
    return {
        "deleted_ids": sorted(delete_ids),
        "deleted_shots": sorted(set(deleted_shots), key=lambda x: int(x)),
        "groups": groups,
    }


def _remove_watermark_corner(content: bytes) -> bytes:
    # [docstring removed]
    try:
        from PIL import Image, ImageDraw
        import io
        img = Image.open(io.BytesIO(content))
        w, h = img.size
        x0 = int(w * 0.80)
        y0 = int(h * 0.91)
        sample_x = x0 + (w - x0) // 2
        sample_y = y0 + (h - y0) // 2
        try:
            bg_color = img.getpixel((sample_x, sample_y))
        except Exception:
            bg_color = (5, 5, 10)  # 默掿黑色
        draw = ImageDraw.Draw(img)
        draw.rectangle([x0, y0, w, h], fill=bg_color)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=95)
        return buf.getvalue()
    except Exception:
        return content  # PIL 不可甗原样返回


def _store_storyboard_asset_image(asset_id, content, toonflow_data):
    import uuid
    fname = f"{uuid.uuid4()}.jpg"
    rel_path = f"/{PROJECT_ID}/storyboard/{fname}"
    full_dir = os.path.join(toonflow_data, "uploads", str(PROJECT_ID), "storyboard")
    os.makedirs(full_dir, exist_ok=True)
    with open(os.path.join(full_dir, fname), "wb") as f:
        f.write(content)
    conn = get_db()
    conn.execute("UPDATE t_assets SET filePath=? WHERE id=?", (rel_path, asset_id))
    conn.commit()
    conn.close()
    return rel_path


def _pick_best_generated_candidate(data_items, candidate_dir, asset_id, prompt=""):
    candidates = []
    for idx, item in enumerate(data_items, start=1):
        gen_url = item.get("url", "") if isinstance(item, dict) else ""
        if not gen_url:
            continue
        img_r = requests.get(gen_url, timeout=30)
        if img_r.status_code != 200:
            continue
        local_path = _save_candidate_image(img_r.content, candidate_dir, asset_id, idx)
        score, issues = _candidate_score(local_path, prompt=prompt)
        phash_val = _phash(img_r.content)
        candidates.append({
            "content": img_r.content,
            "score": score,
            "issues": issues,
            "local_path": local_path,
            "size_kb": len(img_r.content) // 1024,
            "phash": phash_val,
        })
    if not candidates:
        return None
    unique = [candidates[0]]
    for c in candidates[1:]:
        is_dup = any(_hamming_distance(c["phash"], u["phash"]) < 5 for u in unique if c["phash"] and u["phash"])
        if not is_dup:
            unique.append(c)
    if len(unique) < len(candidates):
        pass  # [encoding fixed]


def _candidate_count_for_prompt(prompt, default_count):
    count = max(1, default_count)
    wide_markers = ["wide shot", "wide-angle", "panoramic", "landscape", "establis", "全景", "广角", "远景"]
    if any(marker in (prompt or "") for marker in wide_markers):
        count = max(count + 4, count * 2)
    return min(count, 8)

_translate_cache = {}
_flux_translate_cache = {}


def _translate_prompt_for_flux(prompt):
    # [docstring removed]
    if not prompt:
        return prompt
    import re as _re
    if not _re.search(r'[\u4e00-\u9fff]', prompt):
        return prompt
    cache_key = "flux_" + prompt[:200]
    if cache_key in _flux_translate_cache:
        return _flux_translate_cache[cache_key]
    try:
        key = get_deepseek_key()
        if not key:
            return prompt
        translated = deepseek_call(key, [


            {"role": "user", "content": prompt}
        ], max_tokens=250)
        if len(translated) > 30:
            _flux_translate_cache[cache_key] = translated
            return translated
    except Exception as e:
        logger.warning(f"  FLUX翻译失败: {e}")
    return prompt


def _translate_prompt_to_english(prompt):
    # [docstring removed]
    if not prompt:
        return prompt
    import re as _re
    if not _re.search(r'[\u4e00-\u9fff]', prompt):
        return prompt  # 纋文,无需翻译
    cache_key = prompt[:200]
    if cache_key in _translate_cache:
        return _translate_cache[cache_key]
    try:
        key = get_deepseek_key()
        if not key:
            return prompt
        translated = deepseek_call(key, [
            {"role": "system", "content": "Convert this Chinese anime storyboard description to Danbooru-style tags for NoobAI XL (Pony-based SDXL model). Target style: MAPPA studio (Jujutsu Kaisen / Chainsaw Man) ?gritty dark anime, thick outlines, flat 2D cel shading, NOT photorealistic.\n\nSTRICT RULES:\n- Output ONLY comma-separated Danbooru tags\n- Use underscores for multi-word tags: black_hair, chinese_clothes, stone_floor\n- Include: character count (1boy/1girl/2boys), shot framing (close-up/medium_shot/full_body/from_above), pose, clothing, expression\n- Characters: sharp_jawline, detailed_face, anime_face, expressive_eyes ?NEVER realistic_proportions or mature_male\n- DO NOT add any color/lighting/mood tags ?those are handled separately\n- DO NOT add: golden_hour, sepia, desaturated, warm_lighting, bokeh, depth_of_field\n- DO NOT add: score_9, score_8_up, masterpiece, best_quality, anime_style, mappa ?already in prefix/suffix\n- Focus ONLY on: WHO (character appearance) + WHAT (action/pose) + WHERE (setting/objects)\n- Keep it under 30 tags"},
            {"role": "user", "content": prompt}
        ], max_tokens=200)
        if len(translated) > 20:
            _translate_cache[cache_key] = translated
            return translated
    except Exception as e:
        logger.warning(f"  翻译失败: {e}")
    return prompt


def _save_ref_image_to_comfyui_input(b64_data_uri, name="ref"):
    # [docstring removed]
    import base64
    # 去掉 data:image/xxx;base64, 前缀
    if "," in b64_data_uri:
        b64_raw = b64_data_uri.split(",", 1)[1]
    else:
        b64_raw = b64_data_uri
    img_bytes = base64.b64decode(b64_raw)
    safe_name = re.sub(r'[^\w]', '_', name)
    fname = f"ref_{safe_name}_{int(time.time()) % 100000}.png"
    # Upload via ComfyUI API so the file is accessible regardless of install path
    _upload_sess = requests.Session()
    _upload_sess.trust_env = False
    resp = _upload_sess.post(
        "http://127.0.0.1:8188/upload/image",
        files={"image": (fname, img_bytes, "image/png")},
        data={"overwrite": "true"},
        timeout=15,
    )
    resp.raise_for_status()
    result = resp.json()
    return result.get("name", fname)


def _translate_prompt_noobai(prompt_zh: str, key=None) -> str:
    """Translate Chinese prompt to English Danbooru tags for NoobAI-XL."""
    zh_count = sum(1 for c in prompt_zh if '\u4e00' <= c <= '\u9fff')
    if zh_count < 8:
        return prompt_zh
    if not key:
        try:
            key = get_deepseek_key()
        except Exception:
            pass
    if not key:
        return prompt_zh
    try:
        resp = deepseek_call(key, [
            {"role": "system", "content": (
                "You are a Danbooru tag generator for NoobAI-XL SDXL (MAPPA anime style, dark eastern fantasy). "
                "Translate the Chinese scene description to English Danbooru tags ONLY. "
                "Keep character names as-is (romanized). Include: character count, appearance, action, clothing, "
                "environment, mood, lighting, camera angle. "
                "Output format: comma-separated tags, NO explanation, NO markdown. "
                "Style anchor: dark_background, dramatic_lighting, mature_male, dark_fantasy, anime_coloring, "
                "MAPPA, sharp_lineart, high_contrast"
            )},
            {"role": "user", "content": prompt_zh[:500]}
        ], max_tokens=220)
        translated = resp.strip()
        logger.debug(f"  [translate] ZHEN: {prompt_zh[:60]}... ?{translated[:80]}...")
        return translated
    except Exception as e:
        return prompt_zh


def _build_ipadapter_workflow(prompt, negative_prompt="", width=1024, height=1536,
                            steps=28, cfg=6, seed=-1,
                            checkpoint="NoobAI-XL-v1.1.safetensors",
                            ref_image_name=None, ipadapter_weight=0.55,
                            ipadapter_weight_type="style transfer",
                            sampler_name="euler", scheduler="normal"):
    # [docstring removed]
    if seed < 0:
        seed = int(time.time() * 1000) % (2**32)

    _dark_neg = VISUAL_NEGATIVE
    neg = negative_prompt or _dark_neg
    if "bright" not in neg and "colorful" not in neg:
        neg = neg + ", " + _dark_neg

    if not ref_image_name:
        return {
            "3": {"class_type": "KSampler", "inputs": {
                "seed": seed, "steps": steps, "cfg": cfg,
                "sampler_name": sampler_name, "scheduler": scheduler, "denoise": 1.0,
                "model": ["4", 0], "positive": ["6", 0], "negative": ["7", 0], "latent_image": ["5", 0]}},
            "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": checkpoint}},
            "5": {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
            "6": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["4", 1]}},
            "7": {"class_type": "CLIPTextEncode", "inputs": {"text": neg, "clip": ["4", 1]}},
            "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
            "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "ipadapter_gen", "images": ["8", 0]}}
        }

    return {
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": checkpoint}},
        "10": {"class_type": "CLIPVisionLoader", "inputs": {"clip_name": "CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors"}},
        "11": {"class_type": "IPAdapterModelLoader", "inputs": {"ipadapter_file": "ip-adapter-plus_sd15.safetensors"}},
        "12": {"class_type": "LoadImage", "inputs": {"image": ref_image_name}},
        "13": {"class_type": "CLIPVisionEncode", "inputs": {"clip_vision": ["10", 0], "image": ["12", 0]}},
        "14": {"class_type": "IPAdapterAdvanced", "inputs": {
            "model": ["4", 0], "ipadapter": ["11", 0], "image": ["12", 0],
            "weight": ipadapter_weight, "weight_type": ipadapter_weight_type,
            "combine_embeds": "concat", "start_at": 0.0, "end_at": 1.0,
            "embeds_scaling": "K+V", "clip_vision": ["10", 0]}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["4", 1]}},
        "3": {"class_type": "KSampler", "inputs": {
            "seed": seed, "steps": steps, "cfg": cfg,
            "sampler_name": sampler_name, "scheduler": scheduler, "denoise": 1.0,
            "model": ["14", 0], "positive": ["6", 0], "negative": ["7", 0], "latent_image": ["5", 0]}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "ipadapter_gen", "images": ["8", 0]}}
    }


def _build_flux_ipadapter_workflow(prompt, negative_prompt="", width=832, height=1216,
                                    steps=28, cfg=1.0, seed=-1,
                                    checkpoint="flux1-dev-fp8.safetensors",
                                    ref_image_name=None, ipadapter_weight=0.85,
                                    ref_image_names=None,
                                    scene_ref_name=None, scene_ipadapter_weight=0.3,
                                    prev_frame_name=None, prev_frame_weight=0.2):
    # [docstring removed]
    
    if seed < 0:
        seed = int(time.time() * 1000) % (2**32)

    # 无参考图? ?txt2img FLUX
    if not ref_image_name and not ref_image_names:
        return {
            "10": {"class_type": "UNETLoader", "inputs": {"unet_name": checkpoint, "weight_dtype": "fp8_e4m3fn"}},
            "11": {"class_type": "DualCLIPLoader", "inputs": {
                "clip_name1": "t5xxl_fp8_e4m3fn_scaled.safetensors",
                "clip_name2": "clip_l.safetensors", "type": "flux"}},
            "12": {"class_type": "VAELoader", "inputs": {"vae_name": "flux_ae.safetensors"}},
            "5": {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
            "6": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["11", 0]}},
            "3": {"class_type": "KSampler", "inputs": {
                "seed": seed, "steps": steps, "cfg": cfg,
                "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0,
                "model": ["10", 0], "positive": ["6", 0], "negative": ["6", 0],
                "latent_image": ["5", 0]}},
            "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["12", 0]}},
            "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "flux_gen", "images": ["8", 0]}}
        }

    all_ref_names = []
    if ref_image_names:
        all_ref_names = list(ref_image_names)
    elif ref_image_name:
        all_ref_names = [ref_image_name]

    wf = {
        "10": {"class_type": "UNETLoader", "inputs": {"unet_name": checkpoint, "weight_dtype": "fp8_e4m3fn"}},
        "11": {"class_type": "DualCLIPLoader", "inputs": {
            "clip_name1": "t5xxl_fp8_e4m3fn_scaled.safetensors",
            "clip_name2": "clip_l.safetensors", "type": "flux"}},
        "12": {"class_type": "VAELoader", "inputs": {"vae_name": "flux_ae.safetensors"}},
        "20": {"class_type": "IPAdapterFluxLoader", "inputs": {
            "ipadapter": "ip-adapter.bin",
            "clip_vision": "google/siglip-so400m-patch14-384",
            "provider": "cuda"}},
    }

    prev_model_node = "10"
    for idx, ref_name in enumerate(all_ref_names):
        load_node = f"30_{idx}"
        apply_node = f"21_{idx}"
        wf[load_node] = {"class_type": "LoadImage", "inputs": {"image": ref_name}}
        char_weight = ipadapter_weight if idx == 0 else ipadapter_weight * 0.7
        wf[apply_node] = {"class_type": "ApplyIPAdapterFlux", "inputs": {
            "model": [prev_model_node, 0], "ipadapter_flux": ["20", 0], "image": [load_node, 0],
            "weight": char_weight, "start_percent": 0.0, "end_percent": 1.0}}
        prev_model_node = apply_node

    final_model_node = prev_model_node

    if scene_ref_name:
        wf["scene_load"] = {"class_type": "LoadImage", "inputs": {"image": scene_ref_name}}
        wf["scene_ipa_apply"] = {"class_type": "ApplyIPAdapterFlux", "inputs": {
            "model": [final_model_node, 0], "ipadapter_flux": ["20", 0],
            "image": ["scene_load", 0],
            "weight": scene_ipadapter_weight, "start_percent": 0.0, "end_percent": 1.0}}
        final_model_node = "scene_ipa_apply"

    if prev_frame_name:
        if scene_ref_name:
            merged_weight = max(scene_ipadapter_weight, prev_frame_weight + 0.1)
            wf["prev_load"] = {"class_type": "LoadImage", "inputs": {"image": prev_frame_name}}
            wf["scene_ipa_apply"]["inputs"]["image"] = ["prev_load", 0]
            wf["scene_ipa_apply"]["inputs"]["weight"] = merged_weight
        else:
            wf["prev_load"] = {"class_type": "LoadImage", "inputs": {"image": prev_frame_name}}
            wf["prev_ipa_apply"] = {"class_type": "ApplyIPAdapterFlux", "inputs": {
                "model": [final_model_node, 0], "ipadapter_flux": ["20", 0],
                "image": ["prev_load", 0],
                "weight": prev_frame_weight, "start_percent": 0.0, "end_percent": 1.0}}
            final_model_node = "prev_ipa_apply"
            logger.info(f"  ?IPAdapter前帧反(狫模式): {prev_frame_name} (weight={prev_frame_weight})")

    wf.update({
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["11", 0]}},
        "3": {"class_type": "KSampler", "inputs": {
            "seed": seed, "steps": steps, "cfg": cfg,
            "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0,
            "model": [final_model_node, 0], "positive": ["6", 0], "negative": ["6", 0],
            "latent_image": ["5", 0]}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["12", 0]}},
        "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "flux_ipa_gen", "images": ["8", 0]}}
    })
    return wf


def _build_nova3dcg_workflow(prompt, negative_prompt="", width=832, height=1216,
                             steps=35, cfg=5.0, seed=-1,
                             checkpoint="Nova 3DCG XL_Ill v8.0.safetensors",
                             clip_skip=2,
                             lora_name=None, lora_strength=1.0):
    if seed < 0:
        seed = int(time.time() * 1000) % (2**32)
    # model source node: checkpoint or lora-patched model
    if lora_name:
        wf = {
            "4":  {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": checkpoint}},
            "20": {"class_type": "LoraLoader", "inputs": {
                       "model": ["4", 0], "clip": ["4", 1],
                       "lora_name": lora_name,
                       "strength_model": lora_strength,
                       "strength_clip": lora_strength}},
            "10": {"class_type": "CLIPSetLastLayer", "inputs": {"stop_at_clip_layer": -clip_skip, "clip": ["20", 1]}},
            "6":  {"class_type": "CLIPTextEncode",   "inputs": {"text": prompt,          "clip": ["10", 0]}},
            "7":  {"class_type": "CLIPTextEncode",   "inputs": {"text": negative_prompt, "clip": ["10", 0]}},
            "5":  {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
            "3":  {"class_type": "KSampler",         "inputs": {
                       "model": ["20", 0], "positive": ["6", 0], "negative": ["7", 0],
                       "latent_image": ["5", 0], "seed": seed, "steps": steps, "cfg": cfg,
                       "sampler_name": "dpmpp_2m_sde", "scheduler": "karras", "denoise": 1.0}},
            "8":  {"class_type": "VAEDecode",        "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
            "9":  {"class_type": "SaveImage",        "inputs": {"filename_prefix": "donghua3d", "images": ["8", 0]}},
        }
    else:
        wf = {
            "4":  {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": checkpoint}},
            "10": {"class_type": "CLIPSetLastLayer",        "inputs": {"stop_at_clip_layer": -clip_skip, "clip": ["4", 1]}},
            "6":  {"class_type": "CLIPTextEncode",          "inputs": {"text": prompt,          "clip": ["10", 0]}},
            "7":  {"class_type": "CLIPTextEncode",          "inputs": {"text": negative_prompt, "clip": ["10", 0]}},
            "5":  {"class_type": "EmptyLatentImage",        "inputs": {"width": width, "height": height, "batch_size": 1}},
            "3":  {"class_type": "KSampler",                "inputs": {
                       "model": ["4", 0], "positive": ["6", 0], "negative": ["7", 0],
                       "latent_image": ["5", 0], "seed": seed, "steps": steps, "cfg": cfg,
                       "sampler_name": "euler_ancestral", "scheduler": "karras", "denoise": 1.0}},
            "8":  {"class_type": "VAEDecode",               "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
            "9":  {"class_type": "SaveImage",               "inputs": {"filename_prefix": "nova3dcg", "images": ["8", 0]}},
        }
    return wf


def _generate_sdxl_image(prompt, neg_prompt="", width=832, height=1216,
                          checkpoint="Nova 3DCG XL_Ill v8.0.safetensors",
                          lora_name=None, lora_strength=1.0):
    wf = _build_nova3dcg_workflow(
        prompt=prompt, negative_prompt=neg_prompt,
        width=width, height=height, checkpoint=checkpoint,
        lora_name=lora_name, lora_strength=lora_strength,
    )
    return _submit_comfyui_workflow(wf, timeout_seconds=600)


def _build_flux1_donghua_workflow(prompt, width=832, height=1216,
                                  steps=25, cfg=1.0, seed=-1,
                                  unet_name="flux1-dev-fp8.safetensors",
                                  clip_name1="t5xxl_fp8_e4m3fn_scaled.safetensors",
                                  clip_name2="clip_l.safetensors",
                                  vae_name="flux-vae-bf16.safetensors",
                                  lora_name="seedream-cg-flux-v1.safetensors",
                                  lora_strength=0.8):
    """FLUX.1 Dev FP8 + 3D 国漫 LoRA workflow (Seedream CG style)"""
    if seed < 0:
        seed = int(time.time() * 1000) % (2**32)
    wf = {
        "1": {"class_type": "UNETLoader", "inputs": {
            "unet_name": unet_name, "weight_dtype": "fp8_e4m3fn"}},
        "2": {"class_type": "DualCLIPLoader", "inputs": {
            "clip_name1": clip_name1, "clip_name2": clip_name2, "type": "flux"}},
        "1b": {"class_type": "LoraLoader", "inputs": {
            "model": ["1", 0], "clip": ["2", 0],
            "lora_name": lora_name,
            "strength_model": lora_strength, "strength_clip": lora_strength}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": vae_name}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {
            "text": prompt, "clip": ["1b", 1]}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {
            "width": width, "height": height, "batch_size": 1}},
        "3s": {"class_type": "KSampler", "inputs": {
            "seed": seed, "steps": steps, "cfg": cfg,
            "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0,
            "model": ["1b", 0], "positive": ["6", 0], "negative": ["6", 0],
            "latent_image": ["5", 0]}},
        "8": {"class_type": "VAEDecode", "inputs": {
            "samples": ["3s", 0], "vae": ["3", 0]}},
        "9": {"class_type": "SaveImage", "inputs": {
            "filename_prefix": "flux1_donghua", "images": ["8", 0]}}
    }
    return wf


def _generate_flux1_donghua_image(prompt, width=832, height=1216,
                                   lora_name="seedream-cg-flux-v1.safetensors",
                                   lora_strength=0.8):
    wf = _build_flux1_donghua_workflow(
        prompt=prompt, width=width, height=height,
        lora_name=lora_name, lora_strength=lora_strength,
    )
    return _submit_comfyui_workflow(wf, timeout_seconds=600)


def _build_qwen_image_edit_workflow(prompt, width=832, height=1216,
                                     steps=4, cfg=1.0, seed=-1,
                                     unet_name="qwen_image_edit_2511_fp8_lightning_4steps.safetensors",
                                     clip_name="qwen_2.5_vl_7b_fp8_scaled.safetensors",
                                     vae_name="qwen_image_vae.safetensors"):
    """Qwen-Image-Edit-2511 FP8 Lightning 4步 文生图工作流"""
    if seed < 0:
        seed = int(time.time() * 1000) % (2**32)
    wf = {
        "1": {"class_type": "UNETLoader", "inputs": {
            "unet_name": unet_name, "weight_dtype": "fp8_e4m3fn"}},
        "2": {"class_type": "ModelSamplingAuraFlow", "inputs": {
            "model": ["1", 0], "shift": 3.1}},
        "3": {"class_type": "CLIPLoader", "inputs": {
            "clip_name": clip_name, "type": "qwen_image"}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": vae_name}},
        "5p": {"class_type": "TextEncodeQwenImageEditPlus", "inputs": {
            "clip": ["3", 0], "prompt": prompt}},
        "5n": {"class_type": "ConditioningZeroOut", "inputs": {
            "conditioning": ["5p", 0]}},
        "6p": {"class_type": "FluxKontextMultiReferenceLatentMethod", "inputs": {
            "conditioning": ["5p", 0], "reference_latents_method": "index_timestep_zero"}},
        "6n": {"class_type": "FluxKontextMultiReferenceLatentMethod", "inputs": {
            "conditioning": ["5n", 0], "reference_latents_method": "index_timestep_zero"}},
        "7": {"class_type": "EmptySD3LatentImage", "inputs": {
            "width": width, "height": height, "batch_size": 1}},
        "8": {"class_type": "KSampler", "inputs": {
            "seed": seed, "steps": steps, "cfg": cfg,
            "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0,
            "model": ["2", 0], "positive": ["6p", 0], "negative": ["6n", 0],
            "latent_image": ["7", 0]}},
        "9": {"class_type": "VAEDecode", "inputs": {
            "samples": ["8", 0], "vae": ["4", 0]}},
        "10": {"class_type": "SaveImage", "inputs": {
            "filename_prefix": "qwen_edit", "images": ["9", 0]}}
    }
    return wf


def _generate_qwen_image_edit_image(prompt, width=832, height=1216):
    """生成 Qwen-Image-Edit-2511 FP8 Lightning 角色图"""
    wf = _build_qwen_image_edit_workflow(
        prompt=prompt, width=width, height=height,
    )
    return _submit_comfyui_workflow(wf, timeout_seconds=600)


def _generate_sd15_image(prompt, neg_prompt="", width=640, height=960,
                          checkpoint="guofeng3_v34.safetensors"):
    wf = _build_nova3dcg_workflow(
        prompt=prompt, negative_prompt=neg_prompt,
        width=width, height=height, checkpoint=checkpoint,
        steps=28, cfg=7.0, clip_skip=2,
    )
    wf["3"]["inputs"]["sampler_name"] = "dpm_2"
    wf["3"]["inputs"]["scheduler"] = "karras"
    return _submit_comfyui_workflow(wf, timeout_seconds=180)


def _build_klein_workflow(prompt, negative_prompt="", width=832, height=1216,
                        steps=8, cfg=1.0, seed=-1,
                        unet_name="flux-2-klein-9b-fp8.safetensors",
                        clip_name="qwen_3_8b_fp8mixed.safetensors",
                        vae_name="flux2-vae.safetensors",
                        lora_name="Flux2-Klein-9B-consistency-V2.safetensors",
                        lora_strength=0.8,
                        ref_image_names=None,
                        scene_ref_name=None,
                        prev_frame_name=None,
                        dual_branch=False,
                        multi_image_mode=False):
    # [docstring removed]


    # [docstring removed]
    if seed < 0:
        seed = int(time.time() * 1000) % (2**32)

    wf = {
        # UNET Loader (Klein 9B FP8)
        "1": {"class_type": "UNETLoader", "inputs": {
            "unet_name": unet_name,
            "weight_dtype": "fp8_e4m3fn"}},
        # LoRA Loader
        "1b": {"class_type": "LoraLoader", "inputs": {
            "model": ["1", 0], "clip": ["2", 0],
            "lora_name": lora_name,
            "strength_model": lora_strength,
            "strength_clip": lora_strength}},
        # CLIP Loader (Qwen3 for Flux2 Klein)
        "2": {"class_type": "CLIPLoader", "inputs": {
            "clip_name": clip_name,
            "type": "flux2"}},
        # VAE Loader
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": vae_name}},
        # Positive text encode (Qwen3/Flux2 compatible)
        "4": {"class_type": "CLIPTextEncode", "inputs": {
            "text": prompt, "clip": ["1b", 1]}},
        # Negative text encode (standard fallback)
        "4b": {"class_type": "CLIPTextEncode", "inputs": {
            "text": negative_prompt or "lowres, bad anatomy, text, watermark, logo",
            "clip": ["1b", 1]}},
        # Empty Flux2 Latent
        "5": {"class_type": "EmptyFlux2LatentImage", "inputs": {
            "width": width, "height": height, "batch_size": 1}},
        # KSampler Select
        "7": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
        # Random Noise
        "8": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
    }
    if not dual_branch and not multi_image_mode:
        wf["6"] = {"class_type": "Flux2Scheduler", "inputs": {
            "steps": steps, "width": width, "height": height}}

    # model chain: UNET ?LoRA ?(optional scene/prev ReferenceLatent)
    model_out = "1b"
    # conditioning chain: positive ?(optional char ReferenceLatent)
    cond_out = "4"

    if ref_image_names:
        char_ref_name = ref_image_names[0]
        wf["20"] = {"class_type": "LoadImage", "inputs": {"image": char_ref_name}}
        wf["21"] = {"class_type": "VAEEncode", "inputs": {
            "pixels": ["20", 0], "vae": ["3", 0]}}
        wf["22"] = {"class_type": "ReferenceLatent", "inputs": {
            "conditioning": [cond_out, 0], "latent": ["21", 0]}}
        cond_out = "22"

    if scene_ref_name:
        wf["30"] = {"class_type": "LoadImage", "inputs": {"image": scene_ref_name}}
        wf["31"] = {"class_type": "VAEEncode", "inputs": {
            "pixels": ["30", 0], "vae": ["3", 0]}}
        wf["32"] = {"class_type": "ReferenceLatent", "inputs": {
            "conditioning": [cond_out, 0], "latent": ["31", 0]}}
        cond_out = "32"
        logger.info(f"  ?Klein ReferenceLatent(场景): {scene_ref_name}")

    if prev_frame_name:
        wf["40"] = {"class_type": "LoadImage", "inputs": {"image": prev_frame_name}}
        wf["41"] = {"class_type": "VAEEncode", "inputs": {
            "pixels": ["40", 0], "vae": ["3", 0]}}
        wf["42"] = {"class_type": "ReferenceLatent", "inputs": {
            "conditioning": [cond_out, 0], "latent": ["41", 0]}}
        cond_out = "42"
        logger.info(f"  ?Klein ReferenceLatent(前帧): {prev_frame_name}")

    if multi_image_mode:
        wf["6_multi"] = {"class_type": "Flux2Scheduler", "inputs": {
            "steps": 4, "width": width, "height": height}}
        wf["9_multi"] = {"class_type": "BasicGuider", "inputs": {
            "conditioning": [cond_out, 0], "model": [model_out, 0]}}
        wf["10_multi"] = {"class_type": "SamplerCustomAdvanced", "inputs": {
            "noise": ["8", 0], "guider": ["9_multi", 0], "sampler": ["7", 0],
            "sigmas": ["6_multi", 0], "latent_image": ["5", 0]}}
        wf["11_multi"] = {"class_type": "VAEDecode", "inputs": {
            "samples": ["10_multi", 0], "vae": ["3", 0]}}
        wf["12_multi"] = {"class_type": "SaveImage", "inputs": {
            "filename_prefix": "klein_4step_multi", "images": ["11_multi", 0]}}
    elif dual_branch:
            # [orphan dict fixed]
        wf["9_preview"] = {"class_type": "BasicGuider", "inputs": {
            "conditioning": [cond_out, 0], "model": [model_out, 0]}}
        wf["10_preview"] = {"class_type": "SamplerCustomAdvanced", "inputs": {
            "noise": ["8", 0], "guider": ["9_preview", 0], "sampler": ["7", 0],
            "sigmas": ["6_preview", 0], "latent_image": ["5", 0]}}
        wf["11_preview"] = {"class_type": "VAEDecode", "inputs": {
            "samples": ["10_preview", 0], "vae": ["3", 0]}}
        wf["12_preview"] = {"class_type": "SaveImage", "inputs": {
            "filename_prefix": "klein_8step_preview", "images": ["11_preview", 0]}}

            # [orphan dict fixed]
        wf["9_refined"] = {"class_type": "BasicGuider", "inputs": {
            "conditioning": [cond_out, 0], "model": [model_out, 0]}}
        wf["10_refined"] = {"class_type": "SamplerCustomAdvanced", "inputs": {
            "noise": ["8", 0], "guider": ["9_refined", 0], "sampler": ["7", 0],
            "sigmas": ["6_refined", 0], "latent_image": ["5", 0]}}
        wf["11_refined"] = {"class_type": "VAEDecode", "inputs": {
            "samples": ["10_refined", 0], "vae": ["3", 0]}}
        wf["12_refined"] = {"class_type": "SaveImage", "inputs": {
            "filename_prefix": "klein_16step_refined", "images": ["11_refined", 0]}}
    else:
        wf.update({
            "9": {"class_type": "BasicGuider", "inputs": {
                "conditioning": [cond_out, 0], "model": [model_out, 0]}},
            "10": {"class_type": "SamplerCustomAdvanced", "inputs": {
                "noise": ["8", 0], "guider": ["9", 0], "sampler": ["7", 0],
                "sigmas": ["6", 0], "latent_image": ["5", 0]}},
            "11": {"class_type": "VAEDecode", "inputs": {
                "samples": ["10", 0], "vae": ["3", 0]}},
            "12": {"class_type": "SaveImage", "inputs": {
                "filename_prefix": "klein_gen", "images": ["11", 0]}}
        })

    return wf


def _build_infiniteyou_workflow(prompt, negative_prompt="", width=832, height=1216,
                                steps=28, cfg=1.0, seed=-1,
                                checkpoint="flux1-dev-fp8.safetensors",
                                ref_image_names=None, infusenet_strength=1.0,
                                scene_ref_name=None, scene_ipadapter_weight=0.3,
                                prev_frame_name=None, prev_frame_weight=0.2):
    # [docstring removed]
    
    
    
    
    if seed < 0:
        seed = int(time.time() * 1000) % (2**32)
    
    all_ref_names = list(ref_image_names) if ref_image_names else []
    
    # 无参考图? ?txt2img FLUX (?IPAdapter fallback)
    if not all_ref_names:
        return {
            "10": {"class_type": "UNETLoader", "inputs": {"unet_name": checkpoint, "weight_dtype": "fp8_e4m3fn"}},
            "11": {"class_type": "DualCLIPLoader", "inputs": {
                "clip_name1": "t5xxl_fp8_e4m3fn_scaled.safetensors",
                "clip_name2": "clip_l.safetensors", "type": "flux"}},
            "12": {"class_type": "VAELoader", "inputs": {"vae_name": "flux_ae.safetensors"}},
            "5": {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
            "6": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["11", 0]}},
            "3": {"class_type": "KSampler", "inputs": {
                "seed": seed, "steps": steps, "cfg": cfg,
                "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0,
                "model": ["10", 0], "positive": ["6", 0], "negative": ["6", 0],
                "latent_image": ["5", 0]}},
            "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["12", 0]}},
            "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "flux_gen", "images": ["8", 0]}}
        }
    
    # UNETLoader ?LoRA chain (Realism+AntiBlur) ?InfuseNet + mask 分区
    wf = {
        # UNETLoader (FLUX Dev FP8 from diffusion_models/)
        # [orphan dict fixed]
        # [orphan dict fixed]
            # [orphan dict fixed]
            # [orphan dict fixed]
        # VAELoader
        # [orphan dict fixed]
        # [orphan dict fixed]
            # [orphan dict fixed]
            # [orphan dict fixed]
            # [orphan dict fixed]
        # [orphan dict fixed]
            # [orphan dict fixed]
            # [orphan dict fixed]
            # [orphan dict fixed]
        # IDEmbeddingModelLoader (共享)
        # [orphan dict fixed]
            # [orphan dict fixed]
            # [orphan dict fixed]
            # [orphan dict fixed]
            # [orphan dict fixed]
        # InfuseNetLoader (FP8)
        # [orphan dict fixed]
            # [orphan dict fixed]
    }

    model_out_node = "201"

    if scene_ref_name:
        wf["scene_ipa_loader"] = {"class_type": "IPAdapterFluxLoader", "inputs": {
            "ipadapter": "ip-adapter.bin",
            "clip_vision": "google/siglip-so400m-patch14-384",
            "provider": "cuda"}}
        wf["scene_load"] = {"class_type": "LoadImage", "inputs": {"image": scene_ref_name}}
        wf["scene_ipa_apply"] = {"class_type": "ApplyIPAdapterFlux", "inputs": {
            "model": [model_out_node, 0],
            "ipadapter_flux": ["scene_ipa_loader", 0],
            "image": ["scene_load", 0],
            "weight": scene_ipadapter_weight,
            "start_percent": 0.0,
            "end_percent": 1.0}}
        model_out_node = "scene_ipa_apply"

    if prev_frame_name and scene_ref_name:
        merged_weight = max(scene_ipadapter_weight, prev_frame_weight + 0.1)
        wf["prev_load"] = {"class_type": "LoadImage", "inputs": {"image": prev_frame_name}}
        wf["scene_ipa_apply"]["inputs"]["image"] = ["prev_load", 0]
        wf["scene_ipa_apply"]["inputs"]["weight"] = merged_weight
        model_out_node = "scene_ipa_apply"
    elif prev_frame_name:
        wf["prev_ipa_loader"] = {"class_type": "IPAdapterFluxLoader", "inputs": {
            "ipadapter": "ip-adapter.bin",
            "clip_vision": "google/siglip-so400m-patch14-384",
            "provider": "cuda"}}
        wf["prev_load"] = {"class_type": "LoadImage", "inputs": {"image": prev_frame_name}}
        wf["prev_ipa_apply"] = {"class_type": "ApplyIPAdapterFlux", "inputs": {
            "model": [model_out_node, 0],
            "ipadapter_flux": ["prev_ipa_loader", 0],
            "image": ["prev_load", 0],
            "weight": prev_frame_weight,
            "start_percent": 0.0,
            "end_percent": 1.0}}
        model_out_node = "prev_ipa_apply"
        logger.info(f"  ?前帧反(狫模式): {prev_frame_name} (weight={prev_frame_weight})")

    # CLIPTextEncode + FluxGuidance
    wf["153"] = {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["11", 0]}}
    wf["149"] = {"class_type": "FluxGuidance", "inputs": {"guidance": cfg, "conditioning": ["153", 0]}}
    wf["158"] = {"class_type": "EmptySD3LatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}}
    wf["159"] = {"class_type": "RandomNoise", "inputs": {"noise_seed": seed, "control_after_generate": "randomize"}}
    wf["160"] = {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}}
    wf["161"] = {"class_type": "BasicScheduler", "inputs": {
        "scheduler": "simple", "steps": steps, "denoise": 1.0, "model": [model_out_node, 0]}}

    for idx, ref_name in enumerate(all_ref_names):
        load_node = f"120_{idx}"
        extract_id_node = f"121_{idx}"
        extract_pose_node = f"119_{idx}"
        
        wf[load_node] = {"class_type": "LoadImage", "inputs": {"image": ref_name}}
        wf[extract_id_node] = {"class_type": "ExtractIDEmbedding", "inputs": {
            "face_detector": ["102", 0],
            "arcface_model": ["102", 1],
            "image_proj_model": ["102", 2],
            "image": [load_node, 0]}}
        wf[extract_pose_node] = {"class_type": "ExtractFacePoseImage", "inputs": {
            "face_detector": ["102", 0],
            "image": [load_node, 0],
            "width": width, "height": height}}
    
    num_chars = len(all_ref_names)
    for idx in range(num_chars):
        mask_node = f"mask_{idx}"
        if num_chars == 1:
            wf[mask_node] = {"class_type": "SolidMask", "inputs": {
                "value": 1.0, "width": width, "height": height}}
        else:
            # 先创建全?mask
            full_mask_node = f"fullmask_{idx}"
            wf[full_mask_node] = {"class_type": "SolidMask", "inputs": {
                "value": 1.0, "width": width, "height": height}}
            half_w = width // 2
            x_offset = idx * half_w
            wf[mask_node] = {"class_type": "CropMask", "inputs": {
                "mask": [full_mask_node, 0],
                "x": x_offset, "y": 0,
                "width": half_w, "height": height}}

    prev_positive_node = "149"
    for idx in range(num_chars):
        apply_node = f"163_{idx}"
        mask_node = f"mask_{idx}"
        char_strength = infusenet_strength if idx == 0 else infusenet_strength * 0.8
        
        wf[apply_node] = {"class_type": "InfuseNetApply", "inputs": {
            "positive": [prev_positive_node, 0],
            "id_embedding": [f"121_{idx}", 0],
            "control_net": ["108", 0],
            "image": [f"119_{idx}", 0],
            "strength": char_strength,
            "start_percent": 0.0,
            "end_percent": 1.0,
            "vae": ["12", 0],
            "control_mask": [mask_node, 0]}}
        prev_positive_node = apply_node
    
    wf.update({
        "150": {"class_type": "BasicGuider", "inputs": {
            "model": [model_out_node, 0], "conditioning": [prev_positive_node, 0]}},
        "151": {"class_type": "SamplerCustomAdvanced", "inputs": {
            "noise": ["159", 0], "guider": ["150", 0], "sampler": ["160", 0],
            "sigmas": ["161", 0], "latent_image": ["158", 0]}},
        "152": {"class_type": "VAEDecode", "inputs": {"samples": ["151", 0], "vae": ["12", 0]}},
        "45": {"class_type": "SaveImage", "inputs": {"filename_prefix": "infyou_gen", "images": ["152", 0]}}
    })
    
    return wf


def _is_flux_checkpoint(checkpoint_name):
    # [docstring removed]
    name = (checkpoint_name or "").lower()
    return any(tok in name for tok in ["flux", "fp8", "schnell", "dev"])


def _submit_comfyui_workflow(workflow, timeout_seconds=180, return_all_images=False):
    # [docstring removed]


    _sess = requests.Session()
    _sess.trust_env = False
    try:
        r = _sess.post("http://127.0.0.1:8188/prompt", json={"prompt": workflow}, timeout=20)
        if r.status_code != 200:
            logger.warning(f"  ComfyUI POST {r.status_code}: {r.text[:800]}")
        r.raise_for_status()
        prompt_id = r.json().get("prompt_id")
        if not prompt_id:
            return None
        start_ts = time.time()
        all_images = []
        while time.time() - start_ts < timeout_seconds:
            h = _sess.get(f"http://127.0.0.1:8188/history/{prompt_id}", timeout=15)
            if h.status_code == 200:
                hist = h.json() or {}
                pdata = hist.get(prompt_id) or {}
                job_status = pdata.get("status", {}).get("status_str", "")
                if job_status in ("success", "error"):
                    outputs = pdata.get("outputs") or {}
                    for node_data in outputs.values():
                        for img in node_data.get("images", []) or []:
                            view_r = _sess.get("http://127.0.0.1:8188/view", params={
                                "filename": img.get("filename", ""),
                                "subfolder": img.get("subfolder", ""),
                                "type": img.get("type", "output"),
                            }, timeout=30)
                            if view_r.status_code == 200 and len(view_r.content) > 1000:
                                if return_all_images:
                                    all_images.append(view_r.content)
                                else:
                                    return view_r.content
                    return all_images
            time.sleep(2)
    except Exception as e:
        logger.warning(f"  ️ ComfyUI workflow提交/拉取失败: {e}")
    return None


def _generate_klein_image(prompt, neg_prompt="", ref_image_b64=None, ref_image_name=None, profile=None,
                        ref_image_b64_list=None, matched_roles=None, scene_ref_b64=None,
                        prev_frame_b64=None, dual_branch=False, multi_image_mode=False):
    # [docstring removed]


    if profile is None:
        profile = get_style_profile(PROJECT_ID)

    use_dual_branch = dual_branch or profile.get("dual_branch", False)
    use_multi_image = multi_image_mode

    gen_steps = profile.get("klein_steps", profile.get("steps", 8))
    gen_cfg = profile.get("klein_cfg", profile.get("cfg", 1.0))
    gen_width = profile.get("width", 832)
    gen_height = profile.get("height", 1216)

    final_prompt = prompt
    _wide_tokens = ["extreme long shot", "wide shot", "establishing shot", "aerial view"]
    _medium_tokens = ["medium shot", "waist up", "mid shot"]
    _prompt_lower = prompt.lower()
    is_wide = any(tok in _prompt_lower for tok in _wide_tokens)
    is_medium = any(tok in _prompt_lower for tok in _medium_tokens)
    if is_wide:
        gen_width, gen_height = max(gen_width, gen_height), min(gen_width, gen_height)
    elif is_medium:
        gen_width, gen_height = max(gen_width, gen_height), min(gen_width, gen_height)

    if is_wide:
        final_prompt += ", environment dominant composition, subject occupies less than one third of the frame, observed from a distance"
    elif is_medium:
        final_prompt += ", character visible from waist up, environment and character balanced, cinematic story frame"

    if matched_roles:
        try:
            from character_config import get_identity_cards
            cards = get_identity_cards()
            identity_tags = []
            num_roles_in_prompt = len(matched_roles)
            if is_wide:
                max_identity_roles = 1
                identity_mode = "minimal"
            elif is_medium:
                max_identity_roles = 2 if num_roles_in_prompt >= 2 else 1
                identity_mode = "minimal"
            else:
                max_identity_roles = 2
                identity_mode = "normal"
            for role_name in matched_roles[:max_identity_roles]:
                card = cards.get(role_name)
                if not card:
                    continue
                mini_parts = []
                age_desc = card.get("age", "") + card.get("face", "")
                is_female = any(k in card.get("body", "") for k in ["女", "娜", "苗条", "娇"])
                is_old = any(k in age_desc for k in ["老", "年迈", "白发", "白头"])
                if is_female:
                    mini_parts.append("young woman, feminine, soft delicate features")
                elif is_old:
                    mini_parts.append("elderly old man, weathered wrinkled face, grey beard")
                else:
                    mini_parts.append("young man, masculine face, strong brow ridge, angular jaw")
                hair = card.get("hair", "")
                if hair:
                    hair_short = hair.split(",")[0].strip() if "," in hair else hair[:30]
                    mini_parts.append(hair_short)
                clothing = card.get("clothing", "")
                if clothing and identity_mode != "minimal":
                    clothing_short = clothing.split(",")[0].strip() if "," in clothing else clothing[:30]
                    mini_parts.append(clothing_short)
                if mini_parts:
                    identity_tags.append(", ".join(mini_parts))
            if identity_tags:
                id_str = ", ".join(identity_tags)
                if id_str not in final_prompt:
                    final_prompt = final_prompt + ", " + id_str
                if len(identity_tags) >= 2:
                    multi_hint = "two distinct people visible in the frame, two separate individuals"
                    if multi_hint not in final_prompt:
                        final_prompt = multi_hint + ", " + final_prompt
        except Exception as e:
            logger.warning(f"  ️ 角色躻泅失败: {e}")

    klein_suffix = profile.get("storyboard_suffix", "")
    if klein_suffix and klein_suffix not in final_prompt:
        final_prompt = final_prompt + klein_suffix

    ref_image_names = []
    if ref_image_b64_list:
        for idx, b64_data in enumerate(ref_image_b64_list):
            try:
                saved_name = _save_ref_image_to_comfyui_input(b64_data, f"char{idx}")
                ref_image_names.append(saved_name)
            except Exception as e:
                pass
    elif ref_image_b64 and not ref_image_name:
        try:
            ref_image_name = _save_ref_image_to_comfyui_input(ref_image_b64, "char")
            ref_image_names.append(ref_image_name)
        except Exception as e:
            logger.warning(f"  ️ Klein参图保存失败: {e}")

    scene_ref_name = None
    if scene_ref_b64:
        try:
            scene_ref_name = _save_ref_image_to_comfyui_input(scene_ref_b64, "scene")
            logger.info(f"  ?场景参图已保? {scene_ref_name}")
        except Exception as e:
            logger.warning(f"  ️ 场景参图保存失败: {e}")

    prev_frame_name = None
    if prev_frame_b64:
        try:
            prev_frame_name = _save_ref_image_to_comfyui_input(prev_frame_b64, "prev_frame")
            logger.info(f"  ?前帧反图已保存: {prev_frame_name}")
        except Exception as e:
            logger.warning(f"  ️ 前帧反图保存? {e}")

    workflow = _build_klein_workflow(
        prompt=final_prompt,
        negative_prompt=neg_prompt,
        width=gen_width,
        height=gen_height,
        steps=gen_steps,
        cfg=gen_cfg,
        unet_name=profile.get("klein_unet", "flux-2-klein-9b-fp8.safetensors"),
        clip_name=profile.get("klein_clip", "qwen_3_8b_fp8mixed.safetensors"),
        vae_name=profile.get("klein_vae", "flux2-vae.safetensors"),
        lora_name=profile.get("klein_lora", "Flux2-Klein-9B-consistency-V2.safetensors"),
        lora_strength=profile.get("klein_lora_strength", 0.8),
        ref_image_names=ref_image_names,
        scene_ref_name=scene_ref_name,
        prev_frame_name=prev_frame_name,
        dual_branch=use_dual_branch,
        multi_image_mode=use_multi_image,
    )
    timeout = 60 if use_multi_image else (180 if use_dual_branch else 120)

    if use_dual_branch:
        images = _submit_comfyui_workflow(workflow, timeout_seconds=timeout, return_all_images=True)
        if not images:
            return None
        return _submit_comfyui_workflow(workflow, timeout_seconds=timeout)

    return _submit_comfyui_workflow(workflow, timeout_seconds=timeout)


def _generate_image_fallback(prompt, neg_prompt="", ref_image_b64=None, ref_image_name=None,
                            ref_image_b64_list=None, matched_roles=None, scene_ref_b64=None,
                            prev_frame_b64=None):
    # [docstring removed]
    profile = get_style_profile(PROJECT_ID)
    image_engine = profile.get("image_engine", "klein")
    if image_engine == "klein":
        return _generate_klein_image(
            prompt,
            neg_prompt=neg_prompt,
            ref_image_b64=ref_image_b64,
            ref_image_name=ref_image_name,
            profile=profile,
            ref_image_b64_list=ref_image_b64_list,
            matched_roles=matched_roles,
            scene_ref_b64=scene_ref_b64,
            prev_frame_b64=prev_frame_b64,
        )

    # fallback: SDXL + IPAdapter (for anime projects)
    if ref_image_b64 and not ref_image_name:
        try:
            ref_image_name = _save_ref_image_to_comfyui_input(ref_image_b64, "char")
        except Exception as e:
            logger.warning(f"  ️ 参图保存失败: {e}")
    en_prompt = _translate_prompt_noobai(prompt)
    workflow = _build_ipadapter_workflow(
        prompt=en_prompt,
        negative_prompt=neg_prompt,
        width=profile.get("width", 832),
        height=profile.get("height", 1216),
        steps=profile.get("steps", 20),
        cfg=profile.get("cfg", 6),
        checkpoint=profile.get("checkpoint", "NoobAI-XL-v1.1.safetensors"),
        ref_image_name=ref_image_name,
        ipadapter_weight=profile.get("ipadapter_weight", 0.55),
    )
    return _submit_comfyui_workflow(workflow, timeout_seconds=150)


def insert_storyboard_assets(script_id, chapter_num, scenes, prompts):
    """Insert storyboard asset records into ToonFlow DB. Returns list of asset IDs."""
    conn = get_db()
    # Check for existing storyboard assets for this script
    existing = conn.execute(
        "SELECT id, shotIndex FROM t_assets WHERE scriptId=? AND type='分镜' ORDER BY shotIndex",
        (script_id,)
    ).fetchall()
    conn.close()

    if existing and len(existing) >= len(prompts):
        logger.info(f"  Storyboard assets already exist ({len(existing)} records), reusing")
        return [r[0] for r in existing]

    asset_ids = []
    conn2 = get_db()
    for idx, prompt_data in enumerate(prompts):
        shot_idx = idx + 1
        scene = scenes[idx] if idx < len(scenes) else {}
        scene_title = scene.get("title", f"场景{shot_idx}") if isinstance(scene, dict) else str(scene)
        prompt_text = prompt_data if isinstance(prompt_data, str) else prompt_data.get("prompt", "")

        # Check if already exists for this shot index
        row = conn2.execute(
            "SELECT id FROM t_assets WHERE scriptId=? AND type='分镜' AND shotIndex=?",
            (script_id, shot_idx)
        ).fetchone()
        if row:
            asset_ids.append(row[0])
            continue

        conn2.execute(
            "INSERT INTO t_assets (projectId, scriptId, shotIndex, name, prompt, type) "
            "VALUES (?, ?, ?, ?, ?, '分镜')",
            (PROJECT_ID, script_id, shot_idx, scene_title, prompt_text)
        )
        conn2.commit()
        new_id = conn2.execute("SELECT last_insert_rowid()").fetchone()[0]
        asset_ids.append(new_id)

    conn2.close()
    logger.info(f"  Inserted {len(asset_ids)} storyboard assets (script_id={script_id})")
    return asset_ids


def generate_storyboard_images(s, asset_ids, script_id, chapter_num, image_candidates=1, scene_roles_map=None, auto_dedupe=True):

    char_refs = _load_char_refs(s)
    scene_refs = _load_scene_refs(s)

    conn = get_db()
    conn.row_factory = sqlite3.Row
    storyboards = conn.execute(
        "SELECT id, name, prompt, shotIndex FROM t_assets WHERE id IN ({})".format(
            ",".join("?" * len(asset_ids))), asset_ids
    ).fetchall()
    conn.close()
    storyboard_patches = get_prompt_patches("storyboard")
    neg_prompt = "text, title, watermark, logo, letters, words, Chinese characters, subtitle, caption, writing, stamp, signature, banner, calligraphy, seal, ai generated, ai creation, generated by ai, doubao, 豆包, 豆包AI生成, logo mark, brand mark, corner mark, lower-right watermark, portrait, close-up portrait, bust shot, headshot, upper body portrait, single character poster, character sheet, turnaround sheet, profile sheet, idol poster, fashion pose, clean studio background, centered solo pose, pinup, beauty shot, concept art, splash art, poster art, collage, multi panel, split screen, graphic design, illustration board, character card, cover art, key visual poster, hero pose, glamour portrait, shirtless male model, exposed chest, poster protagonist, center-framed character, staring at camera, posed body language, studio fashion lighting, kpop idol face, delicate feminine male face, androgynous male, beauty makeup, editorial fashion photo, romance drama poster, actor promo still, symmetrical front pose, clean skin beauty retouch, idol makeup, glossy lips, elegant soft boy"
    if storyboard_patches.get("negative"):
        pass
    cinematic_positive = ", cinematic story frame, narrative moment, character integrated into environment, off-center framing, rule of thirds composition, visible architecture and ground plane, subject small in frame, subject occupies less than one third of frame, scene-first composition, environment dominant framing, observational cinema, candid moment, unposed body language, action observed from distance, not looking at camera, no poster-like posing, documentary scene realism"
    toonflow_data = os.path.join(os.environ["APPDATA"], "toonflow-app")
    candidate_dir = os.path.join(str(get_quality_logs_dir()), "storyboard_candidates")

    ref_tracking = {}
    ok, fail = 0, 0
    fallback_shots = []
    scene_roles_map = scene_roles_map or {}
    COMFYUI_COOLDOWN = 3
    COMFYUI_URL = "http://127.0.0.1:8188"

    from image_backends import estimate_cost as _est_cost
    logger.info(f"  图片后: {IMAGE_BACKEND}  预成本: {_est_cost(IMAGE_BACKEND, len(storyboards))}")

    gacha_n = max(1, image_candidates)
    gacha_mode = gacha_n > 1
    GACHA_PASS_SCORE = 500
    SINGLE_PASS_SCORE = 900
    GACHA_MAX_RETRY = 2
    if gacha_mode:
        pass
    os.makedirs(candidate_dir, exist_ok=True)

    prev_frame_b64 = None

    for i, (aid, name, prompt, shot_index) in enumerate(storyboards):
        prompt = (prompt or "") + cinematic_positive
        matched_roles = _normalize_scene_roles(scene_roles_map.get(shot_index), char_refs)
        if not matched_roles:
            matched_roles = _match_roles(prompt, char_refs)
        logger.info(f"  [{i+1}/{len(storyboards)}] {name}" + (f" | 角色: {', '.join(matched_roles)}" if matched_roles else ""))

        ref_b64 = None
        ref_b64_list = []
        if matched_roles and char_refs:
            for r in matched_roles:
                if r in char_refs and char_refs[r].get("b64"):
                    if ref_b64 is None:
                        ref_b64 = char_refs[r]["b64"]
                    ref_b64_list.append(char_refs[r]["b64"])

        scene_ref_b64 = None
        matched_scene = _match_scene_ref(prompt, scene_refs)
        if matched_scene and matched_scene in scene_refs:
            scene_ref_b64 = scene_refs[matched_scene].get("b64")
            if scene_ref_b64 and scene_ref_b64 not in ref_b64_list:
                ref_b64_list.append(scene_ref_b64)

        try:
            best_img = None
            best_score = 9999
            best_issues = []
            all_candidates = []
            blocking = False

            for attempt in range(gacha_n if gacha_mode else 1):
                img_bytes = None
                if IMAGE_BACKEND != "local_comfyui":
                    from image_backends import generate_image as _gen_img
                    img_bytes = _gen_img(
                        IMAGE_BACKEND,
                        prompt_zh=prompt,
                        neg_prompt=neg_prompt,
                        ref_images_b64=ref_b64_list or None,
                        width=get_style_profile(PROJECT_ID).get("width", 832),
                        height=get_style_profile(PROJECT_ID).get("height", 1216),
                    )
                if img_bytes is None:
                    img_bytes = _generate_image_fallback(prompt, neg_prompt, ref_image_b64=ref_b64,
                                                        ref_image_b64_list=ref_b64_list or None,
                                                        matched_roles=matched_roles or None,
                                                        scene_ref_b64=scene_ref_b64,
                                                        prev_frame_b64=prev_frame_b64)
                if not img_bytes:
                    if gacha_mode:
                        continue
                    break

                score, issues = _score_image_bytes(img_bytes, prompt=prompt)
                pass_score = GACHA_PASS_SCORE if gacha_mode else SINGLE_PASS_SCORE
                if gacha_mode:
                    all_candidates.append((score, img_bytes, issues))
                if score <= pass_score and not blocking:
                    best_img = img_bytes
                    best_score = score
                    best_issues = issues
                    if gacha_mode:
                        for _, prev_img, _ in all_candidates[:-1]:
                            _save_candidate_image(prev_img, candidate_dir, aid, len(all_candidates))
                    break
                if score < best_score:
                    best_img = img_bytes
                    best_score = score
                    best_issues = issues
                if not gacha_mode:
                    break

            if best_img and best_score > pass_score:
                for retry_round in range(GACHA_MAX_RETRY):
                    retry_prompt = prompt
                    for iss in best_issues:
                        if "色彩" in iss:
                            retry_prompt += ",色彩浓郁鲜明 vivid saturated colors"
                        elif "人物缺" in iss:
                            retry_prompt += ",人物清晰可见 character clearly visible in frame"
                        elif "主体疑似裁切" in iss:
                            retry_prompt += ",全身入画构图完整 full body visible no cropping"
                        elif "镜头类型不" in iss:
                            retry_prompt += ",严格遵循镜头类型描述 correct shot framing as described"
                    if IMAGE_BACKEND != "local_comfyui":
                        from image_backends import generate_image as _gen_img
                        retry_img = _gen_img(
                            IMAGE_BACKEND,
                            prompt_zh=retry_prompt,
                            neg_prompt=neg_prompt,
                            ref_images_b64=ref_b64_list or None,
                            width=get_style_profile(PROJECT_ID).get("width", 832),
                            height=get_style_profile(PROJECT_ID).get("height", 1216),
                        )
                    else:
                        retry_img = _generate_image_fallback(retry_prompt, neg_prompt, ref_image_b64=ref_b64,
                                                            ref_image_b64_list=ref_b64_list or None,
                                                            matched_roles=matched_roles or None,
                                                            scene_ref_b64=scene_ref_b64)
                    if retry_img:
                        retry_score, retry_issues = _score_image_bytes(retry_img, prompt=prompt)
                        logger.info(f"    Retry result: score={retry_score} {'PASS' if retry_score <= pass_score else 'WARN'}")
                        _save_candidate_image(retry_img, candidate_dir, aid, gacha_n + retry_round + 1)
                        if retry_score < best_score:
                            best_img = retry_img
                            best_score = retry_score
                            best_issues = retry_issues
                        if retry_score <= pass_score:
                            break
                    time.sleep(COMFYUI_COOLDOWN)

            img_bytes = best_img
            if img_bytes:
                _store_storyboard_asset_image(aid, img_bytes, toonflow_data)
                import base64
                prev_frame_b64 = f"data:image/jpeg;base64,{base64.b64encode(img_bytes).decode()}"
                scene_tag = f" +SceneRef({matched_scene})" if matched_scene else ""
                gacha_tag = ""
                logger.info(f"  [{i+1}/{len(storyboards)}] ?{name} (ComfyUI{' +IPAdapter' if ref_b64 else ''}{scene_tag}{gacha_tag}, {len(img_bytes)//1024}KB)")
                ref_tracking[str(shot_index)] = {
                    "ref_used": bool(ref_b64),
                    "gen_path": "comfyui_ipadapter" if ref_b64 else "comfyui",
                    "matched_roles": matched_roles or [],
                    "matched_scene": matched_scene or None,
                    "gacha_score": best_score if gacha_mode else None,
                    "gacha_candidates": gacha_n if gacha_mode else 1,
                }
                ok += 1
                if i < len(storyboards) - 1:
                    if (i + 1) % 5 == 0:
                        try:
                            _free_sess = requests.Session()
                            _free_sess.trust_env = False
                            _free_sess.post(f"{COMFYUI_URL}/free", json={"unload_all": True}, timeout=5)
                            time.sleep(2)
                        except Exception:
                            pass
                    time.sleep(COMFYUI_COOLDOWN)
                continue

            fallback_shots.append(shot_index)

            fallback_ok = False
            try:
                fb_prompt = prompt
                if matched_roles and char_refs:
                    role_intros = [f"[{r}外貌:{char_refs[r]['intro']}]" for r in matched_roles if r in char_refs and char_refs[r].get('intro')]
                    if role_intros:
                        fb_prompt = ", ".join(role_intros) + ", " + prompt
                seg_id_val = 1
                try:
                    conn_fb = get_db()
                    row_fb = conn_fb.execute("SELECT segmentId FROM t_assets WHERE id=?", (aid,)).fetchone()
                    if row_fb and row_fb[0]:
                        seg_id_val = row_fb[0]
                    conn_fb.close()
                except Exception:
                    pass
                r_fb = s.post(BASE + "/storyboard/generateShotImage", json={
                    "scriptId": script_id, "projectId": PROJECT_ID,
                    "segmentId": seg_id_val, "title": name,
                    "x": 1, "y": 1,
                    "cells": [{"prompt": fb_prompt, "weight": 1}]
                }, timeout=120)
                if r_fb.status_code == 200:
                    fb_data = r_fb.json().get("data", {})
                    if isinstance(fb_data, dict) and fb_data.get("type") == "Buffer":
                        buf_bytes = bytes(fb_data["data"])
                        if len(buf_bytes) > 10000:
                            _store_storyboard_asset_image(aid, buf_bytes, toonflow_data)
                            logger.info(f"  [{i+1}/{len(storyboards)}] ?{name} (ToonFlow-generateShotImage, {len(buf_bytes)//1024}KB)")
                            ref_tracking[str(shot_index)] = {
                                "ref_used": False,
                                "gen_path": "fallback-generateShotImage",
                                "matched_roles": matched_roles or [],
                            }
                            ok += 1
                            fallback_ok = True
            except Exception as e_fb:
                logger.warning(f"  [{i+1}/{len(storyboards)}] generateShotImage失败: {e_fb}")

            if fallback_ok:
                continue

            try:
                r2 = s.post(BASE + "/assets/generateAssets", json={
                    "id": 0, "type": "storyboard", "name": name,
                    "prompt": prompt, "projectId": PROJECT_ID, "scriptId": script_id,
                }, timeout=120)
                if r2.status_code == 200:
                    path = r2.json().get("data", {}).get("path", "")
                    if path:
                        clean = path.replace("http://127.0.0.1:60000", "")
                        conn2 = get_db()
                        conn2.execute("UPDATE t_assets SET filePath=? WHERE id=?", (clean, aid))
                        conn2.commit()
                        conn2.close()
                        logger.info(f"  [{i+1}/{len(storyboards)}] ?{name} (ToonFlow-generateAssets)")
                        ref_tracking[str(shot_index)] = {
                            "ref_used": False,
                            "gen_path": "fallback-generateAssets",
                            "matched_roles": matched_roles or [],
                        }
                        ok += 1
                        continue
            except Exception as e2:
                logger.warning(f"  [{i+1}/{len(storyboards)}] generateAssets失败: {e2}")

            ref_tracking[str(shot_index)] = {
                "ref_used": False,
                "gen_path": "failed",
                "matched_roles": matched_roles or [],
            }
            fail += 1
            logger.warning(f"  [{i+1}/{len(storyboards)}] ?{name}")
            log_issue("图片生成失败", shot_index, prompt, name, asset_type="storyboard", chapter=chapter_num, prompt_kind="storyboard_generation")
        except Exception as e:
            ref_tracking[str(shot_index)] = {
                "ref_used": False,
                "gen_path": "error",
                "matched_roles": [],
                "error": str(e),
            }
            fail += 1
            logger.error(f"  [{i+1}/{len(storyboards)}] ?{name}: {e}")
            log_issue("图片生成异常", shot_index, prompt, str(e), asset_type="storyboard", chapter=chapter_num, prompt_kind="storyboard_generation")

    dedupe_result = {"deleted_ids": [], "deleted_shots": [], "groups": []}
    if ok > 1 and auto_dedupe:
        try:
            dedupe_result = _auto_dedupe_storyboards(script_id)
            if dedupe_result["deleted_shots"]:
                for shot_key in dedupe_result["deleted_shots"]:
                    ref_tracking.pop(shot_key, None)
                logger.info(f"  臊去重后陕? {dedupe_result['deleted_shots']}")
        except Exception as e_dd:
            logger.warning(f"  ️ 臊去重跳过: {e_dd}")

    if ok > 0:
        try:
            keep_results = []
            conn_k = get_db()
            for aid in asset_ids:
                row_k = conn_k.execute(
                    "SELECT id, name, filePath, prompt, videoPrompt, duration, projectId, scriptId, segmentId, shotIndex "
                    "FROM t_assets WHERE id=? AND filePath IS NOT NULL", (aid,)
                ).fetchone()
                if row_k and row_k[2]:
                    fp = row_k[2]
                    if fp and not fp.startswith("http"):
                        fp = BASE.rstrip("/") + "/uploads" + ("" if fp.startswith("/") else "/") + fp
                    keep_results.append({
                        "id": row_k[0], "name": row_k[1] or "", "filePath": fp,
                        "prompt": row_k[3] or "", "videoPrompt": row_k[4] or "",
                        "duration": str(row_k[5] or "4"), "projectId": row_k[6] or PROJECT_ID,
                        "scriptId": row_k[7] or script_id, "segmentId": row_k[8] or 1,
                        "shotIndex": row_k[9] or 1, "type": "分镜",
                    })
            conn_k.close()
            if keep_results:
                logger.info(f"  DB已直接回填 {len(keep_results)} 条分镜 filePath (跳过keepStoryboard避免UNIQUE冲突)")
        except Exception as e_k:
            logger.warning(f"  keepStoryboard调用失败(不影响流?: {e_k}")

    if fallback_shots:
        logger.warning(f"  ️ {len(fallback_shots)}丕头走了fallback(无参考图),需定向重修: {fallback_shots}")

    if ref_tracking:
        tracking_dir = str(get_quality_logs_dir())
        os.makedirs(tracking_dir, exist_ok=True)
        tracking_file = os.path.join(tracking_dir, f"ref_tracking_{script_id}.json")
        existing = {}
        if os.path.exists(tracking_file):
            try:
                with open(tracking_file, "r", encoding="utf-8") as f:
                    existing = json.load(f)
            except Exception:
                pass
        existing.update(ref_tracking)
        with open(tracking_file, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)
        ref_used_count = sum(1 for v in ref_tracking.values() if v.get("ref_used"))
        if ref_used_count < len(ref_tracking):
            no_ref = [k for k, v in ref_tracking.items() if not v.get("ref_used")]

    deduped = len(dedupe_result.get("deleted_ids") or [])
    return ok, fallback_shots

def create_video_config(s_http, script_id, chapter_num, chapter_title, video_ai_config_id):
    _synopsis = get_synopsis()
    _genre = get_genre()
    base_prompt = ""
    full_prompt = _style_text("video_prefix", VIDEO_PREFIX) + base_prompt

    # 优先: addVideoConfig API
    try:
        r = s_http.post(BASE + "/video/addVideoConfig", json={
            "scriptId":     script_id,
            "projectId":    PROJECT_ID,
            "configId":     video_ai_config_id,
            "audioEnabled": True,
            "mode":         "single",
            "resolution":   "720:1280",
            "duration":     10,
            "prompt":       full_prompt,
        }, timeout=15)
        if r.status_code == 200:
            data = r.json().get("data", {})
            config_id = data.get("id") if isinstance(data, dict) else None
            if config_id:
                logger.info(f"  ?config_id={config_id} (via API)")
                return config_id
            conn = get_db()
            row = conn.execute(
                "SELECT id FROM t_videoConfig WHERE scriptId=? ORDER BY id DESC LIMIT 1",
                (script_id,)
            ).fetchone()
            conn.close()
            if row:
                logger.info(f"  ?config_id={row[0]} (via API+DB)")
                return row[0]
    except (requests.RequestException, json.JSONDecodeError, sqlite3.Error) as e:
        logger.warning(f"  addVideoConfig API失败, 降级到DB: {e}")

    now = int(time.time() * 1000)
    conn = get_db()
    c    = conn.cursor()
    c.execute(
        "INSERT INTO t_videoConfig "
        "(scriptId, projectId, aiConfigId, audioEnabled, manufacturer, mode, "
        "startFrame, endFrame, images, resolution, duration, prompt, selectedResultId, createTime, updateTime) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (script_id, PROJECT_ID, video_ai_config_id, 1, "doubao", "standard",
        "", "", "[]", "720:1280", 10, full_prompt, 0, now, now)
    )
    config_id = c.lastrowid
    conn.commit()
    conn.close()
    logger.info(f"  ?config_id={config_id} (via DB)")
    return config_id


def generate_video(s, script_id, config_id, ai_config_id):
    logger.info("Step 8: 觏视生成")
    r = s.post(BASE + "/video/generateVideo", json={
        "scriptId":    script_id,
        "projectId":   PROJECT_ID,
        "configId":    config_id,
        "resolution":  "720:1280",
        "aiConfigId":  ai_config_id,
        "filePath":    "",
        "duration":    10,
        "prompt":      "",
        "mode":        "standard",
        "audioEnabled": True,
    }, timeout=60)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"generateVideo失败: {r.text[:200]}")

    data = r.json().get("data", {})
    video_id = data.get("id") if isinstance(data, dict) else None
    logger.info(f"  ?video_id={video_id}")
    return video_id


def wait_for_video(s, script_id, timeout=300):
    logger.info(f"Step 9: 等待视生成完成 (多{timeout}?")
    start = time.time()
    while time.time() - start < timeout:
        r = s.post(BASE + "/video/getVideo", json={"scriptId": script_id})
        if r.status_code == 200:
            videos = r.json().get("data", [])
            if isinstance(videos, list) and videos:
                v = videos[-1]
                state = v.get("state", 0)
                if state == 1:
                    path = v.get("filePath", "")
                    logger.info(f"  ?视完成: {path}")
                    return path
        elapsed = int(time.time() - start)
        logger.info(f"  等待?.. ({elapsed}s)")
        time.sleep(10)
    logger.warning("  ️ 视等待超时")
    return None


def export_video(video_path, chapter_num, chapter_title):
    if not video_path:
        return
    logger.info("Step 10: 导出视")
    import shutil
    uploads = os.path.join(os.environ["APPDATA"], "toonflow-app", "uploads")
    src     = os.path.join(uploads, video_path.lstrip("/"))
    _novel = get_novel_name()
    out_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                       "output", _novel, f"chapter_{chapter_num}")
    os.makedirs(out_dir, exist_ok=True)
    dst = ""
    if os.path.exists(src):
        shutil.copy2(src, dst)
        size_mb = os.path.getsize(dst) / 1024 / 1024
        logger.info(f"  ?导出: {dst} ({size_mb:.1f}MB)")
    else:
        logger.warning(f"  ️ 源文件不存在: {src}")


def run(args):
    logger.info("=" * 60)
    logger.info("=" * 60)
    _set_prompt_trace_context(args.chapter, args.title)
    pipeline_state = _base_pipeline_state(args)
    _save_pipeline_state(pipeline_state)

    # 登录
    from toonflow_provider import ToonFlowClient
    client = ToonFlowClient(base_url=BASE)
    client.login()
    s = client.session
    logger.info("?ToonFlow登录成功")

    global CHARACTERS, _settings_cache
    CHARACTERS = _load_characters_from_db(s)

    key              = get_deepseek_key(s)
    ai_config_id     = get_ai_config_id(s)
    video_ai_cfg_id  = get_video_ai_config_id(s)
    role_appearances = get_role_appearances(s)

    from_step   = args.from_step
    to_step     = args.to_step
    outline_id  = args.outline_id
    script_id   = args.script_id
    scenes      = None
    prompts     = None
    asset_ids   = None
    novel_text  = ""

    if args.novel and os.path.exists(args.novel):
        novel_text = read_novel(args.novel)

    if from_step <= 0 and novel_text and not args.no_novel_import:
        import_novel_to_toonflow(s, args.chapter, args.title, novel_text)

    if from_step <= 1:
        if not novel_text:
            raise ValueError("需要提供 --novel 文件路径")
        outline_id, scenes = create_outline(s, args.chapter, args.title, novel_text, key)
        pipeline_state["outline_id"] = outline_id
        pipeline_state["steps"]["1"] = {"name": "create_outline", "status": "completed", "scene_count": len(scenes or []), "updated_at": int(time.time())}
        _save_pipeline_state(pipeline_state)

    if from_step <= 2 and not script_id:
        script_id = create_script_record(outline_id, args.chapter, args.title)
        pipeline_state["script_id"] = script_id
        pipeline_state["steps"]["2"] = {"name": "create_script_record", "status": "completed", "updated_at": int(time.time())}
        _save_pipeline_state(pipeline_state)

    if from_step <= 3:
        script_content = generate_script(s, outline_id, script_id)
    else:
        script_content = _get_script_content(s, script_id)

    if getattr(args, 'use_agent', False) and from_step <= 4 and to_step >= 5:
        logger.info("=" * 50)
        logger.info("=" * 50)
        token_raw = s.headers.get("Authorization", "")
        token = token_raw.replace("Bearer ", "").strip()
        success, client = run_storyboard_agent_sync(PROJECT_ID, script_id)
        if success:
            pass
        else:
            pass

        if scenes is None:
            conn = get_db()
            row  = conn.execute("SELECT data FROM t_outline WHERE id=?", (outline_id,)).fetchone()
            conn.close()
            if row and row[0]:
                try:
                    od = json.loads(row[0])
                    scenes = _get_outline_storyboard_shots(od)
                except json.JSONDecodeError:
                    scenes = None
        if scenes is None and novel_text and key:
            _, scenes = create_outline(s, args.chapter, args.title, novel_text, key)
        if not scenes:
            scenes = ""
        if args.max_shots and len(scenes) > args.max_shots:
            step = len(scenes) / args.max_shots
            idxs = [int(i * step) for i in range(args.max_shots)]
            scenes = [scenes[i] for i in idxs]
        prompts = generate_storyboard_prompts(key, script_content, scenes, role_appearances, chapter_num=args.chapter)

    # Step 5: 插入分镜资产
    if not getattr(args, 'use_agent', False) and from_step <= 5 and to_step >= 5:
        if scenes is None or prompts is None:
            if scenes is None and outline_id:
                conn = get_db()
                row = conn.execute("SELECT data FROM t_outline WHERE id=?", (outline_id,)).fetchone()
                conn.close()
                if row and row[0]:
                    try:
                        od = json.loads(row[0])
                        scenes = _get_outline_storyboard_shots(od)
                    except json.JSONDecodeError:
                        pass
            if scenes is None:
                scenes = ""
            if args.max_shots and len(scenes) > args.max_shots:
                step_s = len(scenes) / args.max_shots
                idxs = [int(i * step_s) for i in range(args.max_shots)]
                scenes = [scenes[i] for i in idxs]
            if prompts is None:
                script_content = ""
                if script_id:
                    conn = get_db()
                    row = conn.execute("SELECT content FROM t_script WHERE id=?", (script_id,)).fetchone()
                    conn.close()
                    script_content = row[0] if row and row[0] else ""
                prompts = generate_storyboard_prompts(key, script_content, scenes, role_appearances, chapter_num=args.chapter)
        asset_ids = insert_storyboard_assets(script_id, args.chapter, scenes, prompts)
        _auto_img = not getattr(args, 'no_asset_images', False)
        sync_character_assets_to_toonflow(s, regen_all=getattr(args, 'regen_chars', False))
        sync_scene_assets_to_toonflow(scenes, auto_gen_images=_auto_img, regen_all=getattr(args, 'regen_scenes', False), outline_id=outline_id)
        sync_prop_assets_to_toonflow(auto_gen_images=_auto_img, regen_all=getattr(args, 'regen_props', False))
        if getattr(args, 'audit_assets', False):
            audit_assets(auto_clean=True)

    # Step 6: 生成分镜图片 (ComfyUI)
    if not getattr(args, 'use_agent', False) and from_step <= 6 and to_step >= 6:
        if asset_ids is None:
            conn = get_db()
            asset_ids = [r[0] for r in conn.execute(
                "SELECT id FROM t_assets WHERE scriptId=? AND type='分镜' ORDER BY shotIndex",
                (script_id,)
            ).fetchall()]
            conn.close()
        if scenes is None and outline_id:
            conn = get_db()
            row = conn.execute("SELECT data FROM t_outline WHERE id=?", (outline_id,)).fetchone()
            conn.close()
            if row and row[0]:
                try:
                    scenes = _get_outline_storyboard_shots(json.loads(row[0]))
                except Exception:
                    scenes = []
        scene_roles_map = {
            idx: (sc.get("roles") or sc.get("characters") or [])
            for idx, sc in enumerate(scenes or [], start=1)
        }
        if asset_ids:
            gen_ok, fallback_shots = generate_storyboard_images(
                s,
                asset_ids,
                script_id,
                args.chapter,
                image_candidates=args.image_candidates,
                scene_roles_map=scene_roles_map,
                auto_dedupe=not getattr(args, 'no_auto_dedupe', False),
            )
            if fallback_shots:
                logger.info(f"  ℹ️ {len(fallback_shots)}丕头走了ToonFlow fallback: {fallback_shots}")

            try:
                import sys as _sys
                _sys.path.insert(0, os.path.dirname(__file__))
                from storyboard_checker import run_check as _sb_check
                _sb_check(script_id, chapter=args.chapter)
                logger.info("  分镜检查完成,问题已记录到quality/")
            except Exception as _e:
                logger.warning(f"  分镜跳过: {str(_e).encode('ascii','replace').decode()}")
        else:
            logger.error("  ?无分镜资亏生成图片")

    if from_step <= 7 and to_step >= 7 and from_step <= 6:
        _gpu_switch_done = getattr(args, '_gpu_switch_done', False)
        if not _gpu_switch_done:
            # 仅在使用本地 ComfyUI 生图时才需要释放 VRAM
            if IMAGE_BACKEND == "local_comfyui":
                try:
                    _sess = requests.Session()
                    _sess.trust_env = False
                    _sess.post("http://127.0.0.1:8188/free", json={"unload_all": True}, timeout=10)
                    logger.info("  已释放 ComfyUI VRAM，等待5秒...")
                    time.sleep(5)
                except Exception:
                    pass
            else:
                logger.info(f"  图片后端={IMAGE_BACKEND}(云端)，GPU空闲，无需释放VRAM")
            args._gpu_switch_done = True

    if from_step <= 7 and to_step >= 7 and getattr(args, 'strict_audit', False):
        try:
            from storyboard_checker import run_check
            audit_ok = run_check(script_id, sample_count=0, chapter=args.chapter)
            if not audit_ok:
                pass
        except Exception as _audit_e:
            pass

    if from_step <= 7 and to_step >= 7:
        seg_cmd = [
            sys.executable, os.path.join(os.path.dirname(__file__), "batch_video_segments.py"),
            "--script-id", str(script_id),
            "--project-id", str(PROJECT_ID),
            "--style", args.style,
            "--max-shots", str(args.max_shots),
            "--duration", str(args.duration),
        ]
        video_backend = getattr(args, "video_backend", None)
        render_mode = getattr(args, "render_mode", None)
        if video_backend:
            seg_cmd.extend(["--video-backend", str(video_backend)])
        if render_mode:
            seg_cmd.extend(["--render-mode", str(render_mode)])
        logger.info(f"  命令: {' '.join(seg_cmd)}")
        ret = subprocess.run(seg_cmd, cwd=os.path.dirname(os.path.dirname(__file__)))
        if ret.returncode != 0:
            logger.error("  ?batch_video_segments 失败")
        else:
            logger.info("  ?视片生成+合并完成")

        try:
            from video_quality_review import run_review as _vr_review
            import glob as _gl
            _out_base = os.path.join(os.path.dirname(os.path.dirname(__file__)), "output")
            _video_dirs = ""
            if _video_dirs:
                _vr_review(_video_dirs[-1], expected_duration=args.duration, expected_count=args.max_shots, chapter=args.chapter)
                logger.info("  ?视完成,问题已记录?quality/")
            else:
                logger.info("  ℹ️ 有到频片段目录,跳过视")
        except Exception as _e:
            pass

    if from_step <= 8 and to_step >= 8:
        out_base = os.path.join(os.path.dirname(os.path.dirname(__file__)), "output")
        merged_pattern = ""
        merged_dir = os.path.join(out_base, merged_pattern)
        merged_mp4 = ""
        if not os.path.exists(merged_mp4):
            import glob
            matches = ""
            if not matches:
                matches = glob.glob(os.path.join(out_base, "**", f"*{args.title}*合并*.mp4"), recursive=True)
            merged_mp4 = matches[-1] if matches else None
        if merged_mp4 and os.path.exists(merged_mp4):
            logger.info(f"Step 8: 烽台词字幕 ?{merged_mp4}")
            sub_cmd = [
                sys.executable, os.path.join(os.path.dirname(__file__), "add_subtitles.py"),
                "--input", merged_mp4,
                "--script-id", str(script_id),
            ]
            subprocess.run(sub_cmd, cwd=os.path.dirname(os.path.dirname(__file__)))
            logger.info("  ?字幕烽完成")
        else:
            logger.warning("  ️ 有到合并频,跳过字幕")

    # Step 9: TTS配音(Edge-TTS 旁白+角色对白?
    if from_step <= 9 and to_step >= 9:
        import glob
        sub_matches = ""
        if sub_matches:
            target_video = sub_matches[-1]
        else:
            merge_matches = glob.glob(os.path.join(out_base, "**", f"*{args.title}*合并*.mp4"), recursive=True)
            if not merge_matches:
                merge_matches = ""
            target_video = merge_matches[-1] if merge_matches else None

        if target_video and os.path.exists(target_video):
            logger.info(f"Step 9: TTS配音 (Edge-TTS) ?{target_video}")
            try:
                from tts_narration import run_tts_pipeline
                tts_result = run_tts_pipeline(
                    video_path=target_video,
                    script_id=script_id,
                    mode="full",
                    segment_sec=args.duration,
                )
                if tts_result:
                    logger.info(f"  ?TTS配音完成: {tts_result}")
                else:
                    pass
            except ImportError:
                logger.warning("  ️ edge-tts 朮装,跳过配音 (pip install edge-tts)")
            except Exception as e:
                logger.error(f"  ?TTS配音失败: {e}")
        else:
            logger.warning("  ️ 有到频文件,跳过配音")

    if from_step <= 10 and to_step >= 10:
        try:
            import glob as _gl
            _out_base = os.path.join(os.path.dirname(os.path.dirname(__file__)), "output")
            _candidates = sorted(
                _gl.glob(os.path.join(_out_base, "**", "*.mp4"), recursive=True),
                key=os.path.getmtime
            )
            _src = _candidates[-1] if _candidates else None
            if _src and os.path.exists(_src) and "1080p" not in _src:
                logger.info(f"Step 10: Real-ESRGAN 超分辎 ?{os.path.basename(_src)}")
                _up_cmd = [
                    sys.executable,
                    os.path.join(os.path.dirname(__file__), "upscale_video.py"),
                    "--input", _src,
                ]
                ret_up = subprocess.run(_up_cmd, cwd=os.path.dirname(os.path.dirname(__file__)))
                if ret_up.returncode == 0:
                    logger.info("  ?超分辎完成 (1080p)")
                else:
                    logger.warning("  ️ 超分辎失败,?Real-ESRGAN 配置")
            elif _src and "1080p" in _src:
                logger.info("  ℹ️ 1080p 文件已存圼跳过超分")
            else:
                logger.warning("  ️ 有到可超分的频,跳过")
        except Exception as _e:
            pass

    if from_step <= 10 and to_step >= 10:
        try:
            _cfg = load_yaml_config()
            _nv = _cfg.get("nvidia", {})
            if _nv.get("enabled"):
                _safety_model = _nv.get("models", {}).get("content_safety", "nvidia/llama-3.1-nemoguard-8b-content-safety")
                _nv_key = _nv.get("api_key", "")
                _nv_base = _nv.get("base_url", "https://integrate.api.nvidia.com/v1")
                conn = get_db()
                _recent = conn.execute(
                    "SELECT prompt FROM t_assets WHERE projectId=? AND type='分镜' AND prompt IS NOT NULL ORDER BY id DESC LIMIT 5",
                    (PROJECT_ID,)
                ).fetchall()
                conn.close()
                _safety_issues = []
                for (prompt_text,) in _recent:
                    try:
                        _sr = requests.post(f"{_nv_base}/chat/completions", headers={
                            "Authorization": f"Bearer {_nv_key}",
                            "Content-Type": "application/json"
                        }, json={
                            "model": _safety_model,
                            "messages": [{"role": "user", "content": prompt_text[:300]}],
                            "max_tokens": 50,
                            "temperature": 0.1
                        }, timeout=15)
                        if _sr.ok:
                            _sc = _sr.json()["choices"][0]["message"]["content"]
                            if "unsafe" in _sc.lower():
                                _safety_issues.append((prompt_text[:50], _sc[:80]))
                    except Exception:
                        pass
                if _safety_issues:
                    logger.warning(f"  ️ 内安全宠: {len(_safety_issues)}条可能有")
                    for pt, sc in _safety_issues:
                        logger.warning(f"    - {pt}: {sc}")
                else:
                    logger.info("  ?内安全宠通过 (Nvidia NemoGuard)")
        except Exception as _e:
            logger.debug(f"  内安全宠跳过: {_e}")

    pipeline_state["status"] = "completed"
    pipeline_state["outline_id"] = outline_id
    pipeline_state["script_id"] = script_id
    pipeline_state["current_step"] = max(int(pipeline_state.get("current_step") or 0), to_step)
    pipeline_state["steps"]["final"] = {"name": "pipeline_complete", "status": "completed", "updated_at": int(time.time())}
    _save_pipeline_state(pipeline_state)
    logger.info("=" * 60)
    logger.info("=" * 60)


def main():
    p = argparse.ArgumentParser(description="批量章节生成流水线(通用版)")
    p.add_argument("--chapter",    type=int, required=True,  help="章节号, 如 37")
    p.add_argument("--title",      required=True,            help="章节标题, 如'夜幕降临'")
    p.add_argument("--novel",                                help="小说名称(默认自动检测)")
    p.add_argument("--from-step",  type=int, default=0,      help="从第几步开始(0-10)")
    p.add_argument("--to-step",    type=int, default=10,     help="到第几步停止(0-10), 默认全部执行含10超分")
    p.add_argument("--outline-id", type=int,                 help="已有outline_id (from-step>1时)")
    p.add_argument("--script-id",  type=int,                 help="已有script_id (from-step>3时)")
    p.add_argument("--style",           default="anime",     help="视频风格: anime/dark_eastern_anime/realistic/3d 或自定义文字")
    p.add_argument("--max-shots",       type=int, default=50, help="最大分镜数量")
    p.add_argument("--duration",        type=int, default=4,  help="每段视频时长(秒), 默认4")
    p.add_argument("--image-candidates", type=int, default=3, help="每张分镜图生成候选数(自动核选最优/抽卡), 默认3")
    p.add_argument("--gacha",            action="store_true", help="启用抽卡模式(多候选+自动评分)")
    p.add_argument("--strict-audit",     action="store_true", help="严格审核模式")
    p.add_argument("--no-novel-import", action="store_true",  help="跳过导入小说原文到ToonFlow UI(已导入过时用)")
    p.add_argument("--no-asset-images", action="store_true",  help="跳过资产图片生成")
    p.add_argument("--use-agent",       action="store_true",  help="使用Agent模式")
    p.add_argument("--video-backend",   default=None,         help="视频后端: wan22/ltx/ken_burns")
    p.add_argument("--render-mode",     default=None,         help="渲染模式: legacy/wan22")
    p.add_argument("--image-backend",   default=None,         help="图片后端: gpt_image2/local_comfyui/qwen_edit_local/gemini_flash/fal_seedream")
    p.add_argument("--regen-chars",     action="store_true",  help="强制用FLUX写实风重新生成所有角色参考图")
    p.add_argument("--regen-props",     action="store_true",  help="强制用FLUX写实风重新生成所有道具参考图")
    p.add_argument("--regen-scenes",    action="store_true",  help="强制重新生成所有场景背景图, 替换旧场景资产")
    p.add_argument("--audit-assets",    action="store_true",  help="审查并清理非本项目资产")
    p.add_argument("--no-auto-dedupe",  action="store_true",  help="关闭分镜生成后的自动去重")
    args = p.parse_args()
    if args.gacha:
        args.image_candidates = max(args.image_candidates, 5)

# ... (rest of the code remains the same)
    _style_locked = False
    try:
        _cfg = load_yaml_config()
        if _cfg:
            _style_cfg = _cfg.get("style", {})
            if _style_cfg.get("locked", False):
                _locked_style = _style_cfg.get("personal", "dark_eastern_anime")
                if _locked_style in STYLE_PRESETS:
                    VIDEO_PREFIX = STYLE_PRESETS[_locked_style]["video"]
                    SB_SUFFIX    = STYLE_PRESETS[_locked_style]["sb"]
                    _style_profile_cache = None
                    _profile = get_style_profile(PROJECT_ID)
                    _profile["video_prefix"] = VIDEO_PREFIX
                    _profile["storyboard_suffix"] = SB_SUFFIX
                    _style_profile_cache = _profile
                    _style_locked = True
    except Exception:
        pass
    if not _style_locked:
        if args.style in STYLE_PRESETS:
            VIDEO_PREFIX = STYLE_PRESETS[args.style]["video"]
            SB_SUFFIX    = STYLE_PRESETS[args.style]["sb"]
        else:
            pass
        _style_profile_cache = None
        _profile = get_style_profile(PROJECT_ID)
        _profile["video_prefix"] = VIDEO_PREFIX
        _profile["storyboard_suffix"] = SB_SUFFIX
        _style_profile_cache = _profile

    # 应用 image backend
    global IMAGE_BACKEND
    if args.image_backend:
        IMAGE_BACKEND = args.image_backend

    run(args)


if __name__ == "__main__":
    main()

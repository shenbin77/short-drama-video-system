# -*- coding: utf-8 -*-
"""
图片生成模块 - image_backends.py
支持后端:
  apimart_image2 - APIMart GPT Image 2 (主后端, openai-compatible)
  grsai_image2   - GRSAI GPT Image 2 中转 (自动 fallback)

用法:
  from image_backends import generate_image
  img_bytes = generate_image("apimart_image2", prompt_zh="近景平拍，沈无渡跳地...")
"""
import base64, json, logging, os, time
from pathlib import Path
import requests
from runtime_paths import load_yaml_config as load_runtime_yaml_config

os.environ["NO_PROXY"] = os.environ.get("NO_PROXY", "") + ",localhost,127.0.0.1"

logger = logging.getLogger(__name__)


def _aspect_ratio_label(width: int, height: int) -> str:
    if not width or not height:
        return "9:16"
    ratio = width / height
    if ratio < 0.7:
        return "9:16"
    if ratio < 0.9:
        return "3:4"
    if ratio < 1.1:
        return "1:1"
    if ratio < 1.5:
        return "4:3"
    return "16:9"


# ── API Keys 配置路径 ────────────────────────────────────────────
_KEYS_PATH = str(Path(os.path.abspath(__file__)).parent.parent.parent.parent / "config" / "api_keys.json")
_keys_cache = None
_yaml_config_cache = None


def _load_keys() -> dict:
    global _keys_cache
    if _keys_cache is None:
        if os.path.exists(_KEYS_PATH):
            with open(_KEYS_PATH, "r", encoding="utf-8") as f:
                _keys_cache = json.load(f)
        else:
            _keys_cache = {}
    return _keys_cache


def _load_yaml_config() -> dict:
    global _yaml_config_cache
    if _yaml_config_cache is None:
        _yaml_config_cache = load_runtime_yaml_config()
    return _yaml_config_cache


def get_api_key(name: str) -> str:
    """读取 API key，优先环境变量，其次 api_keys.json"""
    env_key = name.upper() + "_API_KEY"
    v = os.environ.get(env_key, "")
    if v:
        return v
    return _load_keys().get(name, "")


# ── 3D国漫风格前缀 (所有后端共用) ──────────────────────────────
_3D_DONGHUA_STYLE = (
    "STYLE: 3D Chinese donghua animation (exactly like 仙逆 Renegade Immortal / "
    "斗破苍穹 Battle Through the Heavens / 完美世界 Perfect World 3D donghua). "
    "This MUST look like a 3D animated donghua screenshot, NOT a photograph, NOT live-action, "
    "NOT realistic. Characters must have smooth 3D-rendered skin (like game CG cutscene), "
    "large expressive anime-influenced eyes with bright highlight reflections, "
    "stylized facial features (small nose, V-shaped chin, high cheekbones), "
    "glossy CG hair with perfect strands. "
    "Render quality: Unreal Engine 5 cinematic, subsurface scattering on skin, "
    "volumetric god rays, particle effects, spiritual energy VFX. "
    "Color grading: rich saturated cinematic colors adapted to the scene mood "
    "(warm golden for daytime, cool blue for night, dramatic red for combat, etc). "
    "ABSOLUTELY NO real human skin texture, NO photograph, NO live-action. "
    "COMPOSITION: Follow cinematic composition — rule of thirds, off-center subjects, "
    "proper look room for character gaze direction, environmental framing elements. "
    "For action scenes: use dynamic diagonal composition and motion blur on edges. "
    "For dialogue/confrontation: split composition with characters on opposing sides. "
    "Never center a character like an ID photo.\n\n"
)


# ── APIMart GPT Image 2 (主后端) ─────────────────────────────────
APIMART_API_BASE = os.environ.get("APIMART_API_URL", "https://api.apimart.ai")
APIMART_API_KEY = os.environ.get("APIMART_API_KEY", "")


def apimart_image2(prompt_zh: str, ref_images_b64: list = None,
                   neg_prompt: str = "", width: int = 832, height: int = 1216) -> bytes | None:
    """
    GPT Image 2 via APIMart API (api.apimart.ai)
    主后端：异步提交 → 轮询结果
    支持参考图 (image_urls: base64 data URI)
    按分辨率计费 (1K/2K/4K), 失败不扣费
    """
    api_key = APIMART_API_KEY or get_api_key("apimart")
    if not api_key:
        logger.warning("  ⚠️ [APIMart] 未配置 API Key (APIMART_API_KEY 环境变量或 api_keys.json)")
        return None

    styled_prompt = _3D_DONGHUA_STYLE + prompt_zh

    # 宽高比映射 → APIMart size 值
    aspect = height / max(width, 1)
    if aspect > 1.3:
        size_ratio = "9:16"
        resolution = "1k"  # 864x1536
    elif aspect < 0.77:
        size_ratio = "16:9"
        resolution = "1k"  # 1536x864
    else:
        size_ratio = "1:1"
        resolution = "1k"  # 1024x1024

    payload = {
        "model": "gpt-image-2",
        "prompt": styled_prompt,
        "n": 1,
        "size": size_ratio,
        "resolution": resolution,
    }

    # 参考图：APIMart 支持 base64 data URI 直接传 (最多16张)
    if ref_images_b64:
        # ref_images_b64 已是从 asset_manager 返回的 data URI 列表
        payload["image_urls"] = ref_images_b64
        logger.info(f"  [APIMart] 附带 {len(ref_images_b64)} 张角色参考图")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        logger.info(f"  [APIMart] 提交生图任务 (prompt: {prompt_zh[:60]}...)")
        r = requests.post(
            f"{APIMART_API_BASE}/v1/images/generations",
            json=payload, headers=headers, timeout=30,
        )
        if r.status_code != 200:
            logger.warning(f"  ⚠️ [APIMart] HTTP {r.status_code}: {r.text[:300]}")
            return None

        data = r.json()
        if data.get("code") != 200:
            logger.warning(f"  ⚠️ [APIMart] 提交失败: {json.dumps(data, ensure_ascii=False)[:300]}")
            return None

        task_id = data["data"][0]["task_id"]
        logger.info(f"  [APIMart] 任务已提交: {task_id}，轮询结果...")

        # 等待10秒后开始轮询 (APIMart 建议10-20秒首次查询延迟)
        time.sleep(10)

        # 轮询结果 (最多 180 秒)
        for poll in range(50):
            time.sleep(3)
            try:
                pr = requests.get(
                    f"{APIMART_API_BASE}/v1/tasks/{task_id}",
                    headers=headers, timeout=45,
                )
                pr.raise_for_status()
            except Exception as poll_err:
                logger.warning(f"  ⚠️ [APIMart] 轮询异常: {poll_err}")
                time.sleep(5)
                continue

            pr_data = pr.json()
            result_data = pr_data.get("data", {})
            status = result_data.get("status", "")
            progress = result_data.get("progress", 0)

            if status == "completed":
                images = result_data.get("result", {}).get("images", [])
                if images:
                    img_url = images[0]["url"][0]  # url 是数组
                    logger.info(f"  [APIMart] 下载图片: {img_url}")
                    img_r = requests.get(img_url, timeout=30)
                    img_r.raise_for_status()
                    logger.info(f"  ✅ [APIMart] 生成成功 ({len(img_r.content)//1024}KB)")
                    return img_r.content
                logger.warning(f"  ⚠️ [APIMart] 完成但无图片数据")
                return None

            elif status == "failed":
                error = result_data.get("error", {}).get("message", "unknown")
                logger.warning(f"  ❌ [APIMart] 生成失败: {error}")
                return None

            # 进度日志 (每10次轮询)
            if poll % 10 == 5:
                logger.info(f"  [APIMart] 等待中... status={status}, progress={progress}%")

        logger.warning(f"  ⚠️ [APIMart] 超时 (180s)")
        return None

    except Exception as e:
        logger.warning(f"  ⚠️ [APIMart] 请求异常: {e}")
        return None


# ── GRSAI 中转 API (GPT Image 2, 自动 fallback) ────────────────
GRSAI_API_BASE = os.environ.get("GRSAI_API_URL", "https://grsai.dakka.com.cn")
GRSAI_API_BASE_BACKUP = "https://grsaiapi.com"  # 备用域名
GRSAI_API_KEY = os.environ.get("GRSAI_API_KEY", "")


def _grsai_session() -> requests.Session:
    """返回带 Retry adapter 的 requests.Session，防 SSL 断连"""
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    session = requests.Session()
    retry = Retry(
        total=3, backoff_factor=2,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=["POST", "GET"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def grsai_gpt_image2(prompt_zh: str, ref_images_b64: list = None,
                     neg_prompt: str = "", width: int = 832, height: int = 1216) -> bytes | None:
    """
    GPT Image 2 via GRSAI 中转 API (grsai.dakka.com.cn)
    作为 APIMart 的 fallback，按积分计费
    SSL断连自动重试，备用域名自动切换
    """
    api_key = GRSAI_API_KEY or get_api_key("grsai")
    if not api_key:
        logger.warning("  ⚠️ [GRSAI] 未配置 API Key")
        return None

    styled_prompt = _3D_DONGHUA_STYLE + prompt_zh

    # 宽高比映射
    aspect = height / max(width, 1)
    if aspect > 1.3:
        aspect_ratio = "9:16"
    elif aspect < 0.77:
        aspect_ratio = "16:9"
    else:
        aspect_ratio = "1:1"

    payload = {
        "model": "gpt-image-2",
        "prompt": styled_prompt,
        "aspectRatio": aspect_ratio,
        "webHook": "-1",      # 立即返回 id，轮询结果
        "shutProgress": True,  # 只要最终结果
    }

    # 参考图：GRSAI 需要 URL，暂不支持 base64 直传
    # TODO: 如需参考图支持，可先上传到临时图床再传 URL
    # if ref_images_b64:
    #     payload["urls"] = [...]

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    try:
        logger.info(f"  [GRSAI] 提交生图任务 (prompt: {prompt_zh[:60]}...)")
        session = _grsai_session()
        # 主域名 → 备用域名自动切换
        for base in (GRSAI_API_BASE, GRSAI_API_BASE_BACKUP):
            try:
                r = session.post(
                    f"{base}/v1/draw/completions",
                    json=payload, headers=headers, timeout=30,
                )
                r.raise_for_status()
                _active_base = base
                break
            except Exception as submit_err:
                if base == GRSAI_API_BASE_BACKUP:
                    raise
                logger.warning(f"  ⚠️ [GRSAI] 主域名失败，切换备用: {submit_err}")
                _active_base = GRSAI_API_BASE_BACKUP
        resp = r.json()

        if resp.get("code") != 0:
            logger.warning(f"  ⚠️ [GRSAI] 提交失败: {resp.get('msg')}")
            return None

        task_id = resp.get("data", {}).get("id", "")
        if not task_id:
            logger.warning(f"  ⚠️ [GRSAI] 未返回任务 ID")
            return None

        logger.info(f"  [GRSAI] 任务已提交: {task_id}，轮询结果...")

        # 轮询结果 (最多 180 秒，超时失败最多重试 3 次)
        poll_fail = 0
        for poll in range(60):
            time.sleep(3)
            try:
                pr = session.post(
                    f"{_active_base}/v1/draw/result",
                    json={"id": task_id}, headers=headers, timeout=45,
                )
                pr.raise_for_status()
            except Exception as poll_err:
                poll_fail += 1
                logger.warning(f"  ⚠️ [GRSAI] 轮询异常({poll_fail}/3): {poll_err}")
                if poll_fail >= 3:
                    logger.warning(f"  ⚠️ [GRSAI] 轮询多次失败，放弃")
                    return None
                time.sleep(5)
                continue

            pr_data = pr.json()
            result = pr_data.get("data", {})
            status = result.get("status", "")
            progress = result.get("progress", 0)

            if status == "succeeded":
                results = result.get("results", [])
                if results:
                    img_url = results[0].get("url", "")
                    if img_url:
                        img_r = session.get(img_url, timeout=30)
                        img_r.raise_for_status()
                        logger.info(f"  ✅ [GRSAI] 生成成功 ({len(img_r.content)//1024}KB)")
                        return img_r.content
                logger.warning(f"  ⚠️ [GRSAI] 成功但无图片 URL")
                return None

            elif status == "failed":
                reason = result.get("failure_reason", result.get("error", "unknown"))
                logger.warning(f"  ❌ [GRSAI] 生成失败: {reason}")
                return None

            if poll % 5 == 4:
                logger.info(f"  [GRSAI] 等待中... progress={progress}%")

        logger.warning(f"  ⚠️ [GRSAI] 超时 (180s)")
        return None

    except Exception as e:
        logger.warning(f"  ⚠️ [GRSAI] 请求异常: {e}")
        return None


# ── 兼容旧 CatGPT-Gateway (已废弃，保留定义避免导入错误) ────────
CATGPT_API_BASE = os.environ.get("CATGPT_API_URL", "http://127.0.0.1:8800")
CATGPT_API_TOKEN = os.environ.get("CATGPT_API_TOKEN", "dummy123")


def gpt_image2(prompt_zh: str, ref_images_b64: list = None,
               neg_prompt: str = "", width: int = 832, height: int = 1216) -> bytes | None:
    """
    [已废弃] CatGPT-Gateway 已下线，请使用 apimart_image2
    保留定义避免旧代码导入错误
    """
    logger.warning("  ⚠️ [GPT Image 2] CatGPT-Gateway 已废弃，自动切换 APIMart...")
    return None  # 让 generate_image 自动 fallback


BACKENDS = ["apimart_image2", "grsai_image2"]


def nvidia_vision_audit(image_b64: str, prompt: str = "") -> list:
    """已删除——保留定义避免导入错误"""
    return []  # stub


def generate_image(backend: str, prompt_zh: str, neg_prompt: str = "",
                   ref_images_b64: list = None,
                   width: int = 832, height: int = 1216) -> bytes | None:
    """
    统一图片生成接口
    backend: apimart_image2 | grsai_image2
    ref_images_b64: list of data URI strings (角色参考图)
    返回: image bytes 或 None

    Fallback 链: apimart_image2 → grsai_image2
    """
    # 如果是 apimart_image2 或旧名称 gpt_image2，走 APIMart 主后端 + GRSAI fallback
    need_apimart = backend in ("apimart_image2", "gpt_image2")

    if need_apimart:
        result = apimart_image2(prompt_zh, ref_images_b64=ref_images_b64,
                                neg_prompt=neg_prompt, width=width, height=height)
        if result is None:
            logger.info("  🔄 [APIMart] 失败，自动切换 GRSAI 中转...")
            result = grsai_gpt_image2(prompt_zh, ref_images_b64=ref_images_b64,
                                      neg_prompt=neg_prompt, width=width, height=height)
        return result

    elif backend == "grsai_image2":
        return grsai_gpt_image2(prompt_zh, ref_images_b64=ref_images_b64,
                                neg_prompt=neg_prompt, width=width, height=height)
    else:
        logger.warning(f"  ⚠️ 未知 image backend: {backend}，默认用 apimart_image2")
        return apimart_image2(prompt_zh, ref_images_b64=ref_images_b64,
                              neg_prompt=neg_prompt, width=width, height=height)


def estimate_cost(backend: str, n_images: int) -> str:
    """返回成本估算字符串"""
    costs = {
        "apimart_image2": "积分制 (APIMart GPT Image 2, ~60s/张, 失败自动转GRSAI)",
        "grsai_image2":   "积分制 (GRSAI 中转, GPT Image 2)",
    }
    return costs.get(backend, "未知")

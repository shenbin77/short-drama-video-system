# -*- coding: utf-8 -*-
"""
ComfyUI → ToonFlow 图片API桥接服务
将ComfyUI包装为OpenAI Image API兼容格式，供ToonFlow调用

用法:
  python comfyui_bridge.py              # 默认 localhost:8288
  python comfyui_bridge.py --port 8288  # 指定端口

ToonFlow配置:
  baseUrl: http://localhost:8288/v1
  model: anything-v5
  manufacturer: comfyui
"""

import os
import sys
import json
import time
import uuid
import base64
import logging
import argparse
import urllib.request
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from io import BytesIO
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

COMFYUI_URL = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188")
BRIDGE_PORT = int(os.environ.get("COMFYUI_BRIDGE_PORT", "8288"))

# ============ ComfyUI API 调用 ============

def comfyui_post(path, data):
    """POST to ComfyUI API"""
    url = COMFYUI_URL + path
    payload = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as resp:
        return json.loads(resp.read())


def comfyui_get(path):
    """GET from ComfyUI API"""
    url = COMFYUI_URL + path
    with urllib.request.urlopen(url, timeout=60) as resp:
        return resp.read()


def build_txt2img_workflow(prompt, negative_prompt="", width=1024, height=1536,
                           steps=28, cfg=6.0, seed=-1, checkpoint="NoobAI-XL-v1.1.safetensors",
                           sampler_name="euler_ancestral", scheduler="normal",
                           lightning_lora="", lightning_lora_strength=1.0):
    """构建txt2img ComfyUI workflow (API format)"""
    if seed < 0:
        seed = int(time.time() * 1000) % (2**32)

    workflow = {
        "3": {  # KSampler
            "class_type": "KSampler",
            "inputs": {
                "seed": seed,
                "steps": steps,
                "cfg": cfg,
                "sampler_name": sampler_name,
                "scheduler": scheduler,
                "denoise": 1.0,
                "model": ["4", 0],
                "positive": ["6", 0],
                "negative": ["7", 0],
                "latent_image": ["5", 0]
            }
        },
        "4": {  # Load Checkpoint
            "class_type": "CheckpointLoaderSimple",
            "inputs": {
                "ckpt_name": checkpoint
            }
        },
        "5": {  # Empty Latent Image
            "class_type": "EmptyLatentImage",
            "inputs": {
                "width": width,
                "height": height,
                "batch_size": 1
            }
        },
        "6": {  # CLIP Text Encode (positive)
            "class_type": "CLIPTextEncode",
            "inputs": {
                "text": prompt,
                "clip": ["4", 1]
            }
        },
        "7": {  # CLIP Text Encode (negative)
            "class_type": "CLIPTextEncode",
            "inputs": {
                "text": negative_prompt or "lowres, bad anatomy, bad hands, text, error, missing fingers, extra digit, fewer digits, cropped, worst quality, low quality, normal quality, jpeg artifacts, signature, watermark",
                "clip": ["4", 1]
            }
        },
        "8": {  # VAE Decode
            "class_type": "VAEDecode",
            "inputs": {
                "samples": ["3", 0],
                "vae": ["4", 2]
            }
        },
        "9": {  # Save Image
            "class_type": "SaveImage",
            "inputs": {
                "filename_prefix": "comfyui_bridge",
                "images": ["8", 0]
            }
        }
    }

    # 注入 Lightning LoRA 加速节点
    if lightning_lora:
        workflow["20"] = {
            "class_type": "LoraLoader",
            "inputs": {
                "lora_name": lightning_lora,
                "strength_model": lightning_lora_strength,
                "strength_clip": lightning_lora_strength,
                "model": ["4", 0],
                "clip": ["4", 1]
            }
        }
        workflow["3"]["inputs"]["model"] = ["20", 0]
        workflow["6"]["inputs"]["clip"] = ["20", 1]
        workflow["7"]["inputs"]["clip"] = ["20", 1]

    return workflow


def build_flux_txt2img_workflow(prompt, width=832, height=1216, steps=20, cfg=1.0, seed=-1,
                                checkpoint="majicflus_v10.safetensors"):
    """构建 FLUX txt2img workflow (DualCLIPLoader + VAELoader)"""
    if seed < 0:
        seed = int(time.time() * 1000) % (2**32)
    return {
        "10": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": checkpoint}},
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
        "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "comfyui_bridge_flux", "images": ["8", 0]}}
    }


def _is_flux_model(model_name):
    """判断模型是否为 FLUX 架构"""
    return any(kw in model_name.lower() for kw in ["flux", "majicflus"])


def generate_image(prompt, negative_prompt="", width=768, height=1024,
                   steps=20, cfg=7.0, seed=-1, checkpoint="anything-v5.safetensors"):
    """调用ComfyUI生成图片，返回base64编码的PNG"""
    if _is_flux_model(checkpoint):
        workflow = build_flux_txt2img_workflow(
            prompt=prompt, width=width, height=height, steps=steps,
            cfg=1.0, seed=seed, checkpoint=checkpoint
        )
    else:
        workflow = build_txt2img_workflow(
            prompt=prompt, negative_prompt=negative_prompt,
            width=width, height=height, steps=steps, cfg=cfg,
            seed=seed, checkpoint=checkpoint
        )

    # 提交任务
    result = comfyui_post("/api/prompt", {"prompt": workflow})
    prompt_id = result.get("prompt_id")
    logger.info(f"Submitted to ComfyUI: prompt_id={prompt_id}")

    # 轮询等待完成 (FLUX ~40s/img, SDXL ~5s/img)
    max_wait = 300 if _is_flux_model(checkpoint) else 120
    for _ in range(max_wait):
        time.sleep(1)
        history_raw = comfyui_get(f"/api/history/{prompt_id}")
        history = json.loads(history_raw) if isinstance(history_raw, bytes) else history_raw
        if prompt_id in history:
            outputs = history[prompt_id].get("outputs", {})
            for node_id, node_output in outputs.items():
                if "images" in node_output:
                    for img_info in node_output["images"]:
                        filename = img_info["filename"]
                        subfolder = img_info.get("subfolder", "")
                        img_type = img_info.get("type", "output")
                        # 下载图片
                        params = urllib.parse.urlencode({
                            "filename": filename,
                            "subfolder": subfolder,
                            "type": img_type
                        })
                        img_data = comfyui_get(f"/api/view?{params}")
                        img_b64 = base64.b64encode(img_data).decode("utf-8")
                        logger.info(f"Image generated: {filename} ({len(img_data)} bytes)")
                        return img_b64, filename
    raise TimeoutError(f"ComfyUI generation timed out ({max_wait}s)")


def build_ipadapter_workflow(prompt, negative_prompt="", width=1024, height=1536,
                             steps=28, cfg=6.0, seed=-1,
                             checkpoint="NoobAI-XL-v1.1.safetensors",
                             ipadapter_model="ip-adapter-plus_sd15.safetensors",
                             clip_vision_model="CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors",
                             ref_image_path=None, ipadapter_weight=0.55,
                             ipadapter_weight_type="style transfer"):
    """构建 IP-Adapter 参考图驱动的 ComfyUI workflow (API format)
    
    ref_image_path: 参考图本地路径，None 则退回纯 txt2img
    ipadapter_weight: 参考图影响权重 (0.0-1.0)，越高越接近参考图
    ipadapter_weight_type: 参考图权重类型，推荐 style transfer / composition / standard
    """
    if seed < 0:
        seed = int(time.time() * 1000) % (2**32)

    # 如果没有参考图，退回纯 txt2img
    if not ref_image_path or not os.path.isfile(ref_image_path):
        return build_txt2img_workflow(prompt, negative_prompt, width, height,
                                     steps, cfg, seed, checkpoint)

    workflow = {
        # Load Checkpoint
        "4": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {
                "ckpt_name": checkpoint
            }
        },
        # CLIP Vision Loader
        "10": {
            "class_type": "CLIPVisionLoader",
            "inputs": {
                "clip_name": clip_vision_model
            }
        },
        # IP-Adapter Model Loader
        "11": {
            "class_type": "IPAdapterModelLoader",
            "inputs": {
                "ipadapter_file": ipadapter_model
            }
        },
        # Load Reference Image
        "12": {
            "class_type": "LoadImage",
            "inputs": {
                "image": ref_image_path
            }
        },
        # CLIP Vision Encode
        "13": {
            "class_type": "CLIPVisionEncode",
            "inputs": {
                "clip_vision": ["10", 0],
                "image": ["12", 0]
            }
        },
        # IP-Adapter Apply
        "14": {
            "class_type": "IPAdapterAdvanced",
            "inputs": {
                "model": ["4", 0],
                "ipadapter": ["11", 0],
                "image": ["12", 0],
                "weight": ipadapter_weight,
                "weight_type": ipadapter_weight_type,
                "combine_embeds": "concat",
                "start_at": 0.0,
                "end_at": 1.0,
                "embeds_scaling": "K+V",
                "clip_vision": ["10", 0]
            }
        },
        # Empty Latent Image
        "5": {
            "class_type": "EmptyLatentImage",
            "inputs": {
                "width": width,
                "height": height,
                "batch_size": 1
            }
        },
        # CLIP Text Encode (positive)
        "6": {
            "class_type": "CLIPTextEncode",
            "inputs": {
                "text": prompt,
                "clip": ["4", 1]
            }
        },
        # CLIP Text Encode (negative)
        "7": {
            "class_type": "CLIPTextEncode",
            "inputs": {
                "text": negative_prompt or "lowres, bad anatomy, bad hands, text, error, missing fingers, extra digit, fewer digits, cropped, worst quality, low quality, normal quality, jpeg artifacts, signature, watermark",
                "clip": ["4", 1]
            }
        },
        # KSampler (uses IP-Adapter modified model)
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "seed": seed,
                "steps": steps,
                "cfg": cfg,
                "sampler_name": "euler_ancestral",
                "scheduler": "normal",
                "denoise": 1.0,
                "model": ["14", 0],
                "positive": ["6", 0],
                "negative": ["7", 0],
                "latent_image": ["5", 0]
            }
        },
        # VAE Decode
        "8": {
            "class_type": "VAEDecode",
            "inputs": {
                "samples": ["3", 0],
                "vae": ["4", 2]
            }
        },
        # Save Image
        "9": {
            "class_type": "SaveImage",
            "inputs": {
                "filename_prefix": "ipadapter_gen",
                "images": ["8", 0]
            }
        }
    }
    return workflow


def generate_image_with_ref(prompt, negative_prompt="", width=1024, height=1536,
                            steps=28, cfg=6.0, seed=-1,
                            checkpoint="NoobAI-XL-v1.1.safetensors",
                            ref_image_path=None, ipadapter_weight=0.55,
                            ipadapter_weight_type="style transfer"):
    """调用ComfyUI生成图片（支持IP-Adapter参考图），返回base64编码的PNG"""
    workflow = build_ipadapter_workflow(
        prompt=prompt, negative_prompt=negative_prompt,
        width=width, height=height, steps=steps, cfg=cfg,
        seed=seed, checkpoint=checkpoint,
        ref_image_path=ref_image_path,
        ipadapter_weight=ipadapter_weight,
        ipadapter_weight_type=ipadapter_weight_type
    )

    # 提交任务
    result = comfyui_post("/api/prompt", {"prompt": workflow})
    prompt_id = result.get("prompt_id")
    logger.info(f"Submitted to ComfyUI (IPAdapter): prompt_id={prompt_id}")

    # 轮询等待完成
    for _ in range(180):  # IP-Adapter 可能更慢，最多等180秒
        time.sleep(1)
        try:
            history_raw = comfyui_get(f"/api/history/{prompt_id}")
            history = json.loads(history_raw) if isinstance(history_raw, bytes) else history_raw
        except Exception:
            continue
        if prompt_id in history:
            outputs = history[prompt_id].get("outputs", {})
            for node_id, node_output in outputs.items():
                if "images" in node_output:
                    for img_info in node_output["images"]:
                        filename = img_info["filename"]
                        subfolder = img_info.get("subfolder", "")
                        img_type = img_info.get("type", "output")
                        params = urllib.parse.urlencode({
                            "filename": filename,
                            "subfolder": subfolder,
                            "type": img_type
                        })
                        img_data = comfyui_get(f"/api/view?{params}")
                        img_b64 = base64.b64encode(img_data).decode("utf-8")
                        logger.info(f"Image generated (IPAdapter): {filename} ({len(img_data)} bytes)")
                        return img_b64, filename
    raise TimeoutError("ComfyUI IPAdapter generation timed out (180s)")  # loop is hardcoded range(180), value matches


# ============ OpenAI Image API 兼容服务 ============

class ImageAPIHandler(BaseHTTPRequestHandler):
    """模拟 OpenAI /v1/images/generations 端点"""

    def do_POST(self):
        if self.path == "/v1/images/generations":
            self._handle_generate()
        else:
            self.send_error(404, f"Unknown path: {self.path}")

    def do_GET(self):
        if self.path == "/v1/models":
            self._handle_models()
        elif self.path == "/health" or self.path == "/":
            self._respond_json({"status": "ok", "backend": "comfyui_bridge"})
        else:
            self.send_error(404)

    def _handle_generate(self):
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(content_length))

            prompt = body.get("prompt", "")
            size = body.get("size", "768x1024")
            n = body.get("n", 1)
            model = body.get("model", "anything-v5.safetensors")

            # 解析尺寸
            parts = size.split("x")
            width = int(parts[0]) if len(parts) == 2 else 768
            height = int(parts[1]) if len(parts) == 2 else 1024

            # 确保模型名有后缀
            if not model.endswith(".safetensors") and not model.endswith(".ckpt"):
                model = model + ".safetensors"

            logger.info(f"Generate: {width}x{height}, model={model}, prompt={prompt[:80]}...")

            images = []
            for i in range(n):
                img_b64, filename = generate_image(
                    prompt=prompt, width=width, height=height,
                    checkpoint=model
                )
                images.append({
                    "b64_json": img_b64,
                    "revised_prompt": prompt
                })

            self._respond_json({
                "created": int(time.time()),
                "data": images
            })
        except Exception as e:
            logger.error(f"Generate failed: {e}")
            self._respond_json({"error": {"message": str(e)}}, status=500)

    def _handle_models(self):
        # 列出可用模型
        # NOTE: Windows hardcoded path, for Linux use os.path.expanduser() alternative
        checkpoints_dir = os.path.join("E:\\视频项目", "Layer_5_Engines", "ComfyUI", "models", "checkpoints")
        models = []
        if os.path.exists(checkpoints_dir):
            for f in os.listdir(checkpoints_dir):
                if f.endswith((".safetensors", ".ckpt")):
                    models.append({"id": f, "object": "model", "owned_by": "comfyui"})
        self._respond_json({"data": models})

    def _respond_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def log_message(self, format, *args):
        logger.info(f"[HTTP] {args[0]}")


def main():
    parser = argparse.ArgumentParser(description="ComfyUI → OpenAI Image API Bridge")
    parser.add_argument("--port", type=int, default=BRIDGE_PORT, help="Bridge port (default 8288)")
    parser.add_argument("--comfyui", default=COMFYUI_URL, help="ComfyUI URL")
    args = parser.parse_args()

    _set_comfyui_url(args.comfyui)

    server = HTTPServer(("0.0.0.0", args.port), ImageAPIHandler)
    logger.info(f"🎨 ComfyUI Bridge started on http://0.0.0.0:{args.port}")
    logger.info(f"   ComfyUI backend: {COMFYUI_URL}")
    logger.info(f"   ToonFlow config: baseUrl=http://localhost:{args.port}/v1")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down")
        server.shutdown()


def _set_comfyui_url(url):
    global COMFYUI_URL
    COMFYUI_URL = url


if __name__ == "__main__":
    main()

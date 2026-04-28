# 项目全面审计报告 — 2026-04-27 (已执行全云端迁移)

> **最终结果: 241 GB → 3.4 GB | 回收 237.6 GB | 全云端 V11.0 架构**

## 一、磁盘空间总览 (清理前 241 GB → 清理后 3.4 GB)

| 目录 | 大小 | 说明 |
|------|------|------|
| **Layer_5_Engines/comfyui** | **121 GB** | ComfyUI + 模型 (115 GB 模型) |
| **Layer_5_Engines/video/ltx-desktop** | **106 GB** | LTX 2.3 Desktop (含模型) |
| Layer_5_Engines/audio/qwen3-tts | 9 GB | Qwen3 TTS 语音模型 |
| AI_Tools/CatGPT-Gateway | 992 MB | GPT Image 2 后端 (.venv 占大头) |
| 即梦/Dreamina Pro Max | 1.17 GB | DPM 破解版 (含 python_portable) |
| 打包发布 | 438 MB | 已打包的3个工具 |
| AI_Tools/jimeng 系列 | 272 MB | 即梦 API 工具 (多个重复) |
| scripts | 250 MB | 脚本+测试输出 |
| dreamina_auto_profiles | 144 MB | Dreamina 浏览器 profiles |
| Layer_6_Services | 102 MB | 核心流水线代码 |
| 其他 (docs, config, agents...) | < 50 MB | 配置/文档/小工具 |

## 二、当前生产后端配置

| 功能 | 当前后端 | 本地/云端 | 状态 |
|------|---------|----------|------|
| **图片生成** | `gpt_image2` (CatGPT-Gateway) | ☁️ 云端 ChatGPT | ✅ 生产主力 |
| **视频生成** | `ltx23_desktop` | 💻 本地 3090 | ✅ 生产主力 |
| **视频生成(备选)** | `seedance` (Dreamina 首尾帧) | ☁️ 云端 | ✅ 可用 |
| **TTS 语音** | `qwen3-tts` | 💻 本地 | ✅ 可用 |
| **LLM 文本** | DeepSeek/Gemini (one-api) | ☁️ 云端 API | ✅ 可用 |
| **角色参考图** | ComfyUI + InfiniteYou + FLUX | 💻 本地 3090 | ✅ 可用 |

### 结论：图片走云端(GPT Image 2)，视频走本地(LTX 2.3)，TTS走本地

---

## 三、可删除的资源 (预估可回收 ~110 GB)

### 🔴 高优先 — 确认已弃用可安全删除

| 项目 | 大小 | 理由 |
|------|------|------|
| `ComfyUI/models/diffusion_models/qwen_image_edit_*.safetensors` (2个) | **38 GB** | 图片已转 GPT Image 2 云端，qwen_edit 本地不再需要 |
| `ComfyUI/models/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors` | **8.7 GB** | qwen_edit 配套 encoder，一起删 |
| `ComfyUI/models/text_encoders/qwen_3_8b_fp8mixed.safetensors` | **8 GB** | qwen_edit 配套 encoder |
| `ComfyUI/models/loras/Qwen-Image-Edit-*.safetensors` | **0.8 GB** | qwen_edit 配套 LoRA |
| `ComfyUI/models/diffusion_models/flux-2-klein-9b-fp8.safetensors` | **8.8 GB** | FLUX Klein，已有 flux1-dev 足够 |
| `ComfyUI/models/loras/Flux2-Klein-*-consistency*.safetensors` | **0.3 GB** | Klein 配套 |
| `AI_Tools/jimeng-free-api-all` | **102 MB** | 已被 DPM/首尾帧工具替代 |
| `AI_Tools/iptag-jimeng-api` + `jimeng-api-iptag` | **170 MB** | 重复的即梦 API，已弃用 |
| `AI_Tools/seedance-api` + `dreamina2api` + `dreamina-api-lijunyi` | **0.6 MB** | 零散试验代码 |
| `AI_Tools/Dreamina_auto_Sign` + `dreamina-auto-sign` | **0.1 MB** | 已被 DPM 替代 |
| `dreamina_auto_profiles/` | **144 MB** | 旧版自动化浏览器 profiles |
| `dreamina_browser_profile/` | **33 MB** | 旧版浏览器 profile |
| `scripts/_archive/` | **74 MB** | 已归档的旧脚本 |
| `scripts/test_output/` | **180 MB** | 测试输出 (截图+视频) |
| `_tmp_scripts/` | **3 MB** | 临时脚本 |
| `即梦/DPM/backups/` | **31 MB** | 账号备份 |
| `ComfyUI/output/` | **670 MB** | 旧生成的图片 |
| `Layer_5_Engines/video/lightx2v/` | **19 MB** | WAN 2.2 已删除，残留配置 |

**预计可回收: ~66 GB (qwen_edit 模型) + 8.8 GB (Klein) + 1.4 GB (杂项) ≈ 76 GB**

### 🟡 中优先 — 需确认是否还要本地角色参考图

| 项目 | 大小 | 说明 |
|------|------|------|
| `ComfyUI/models/diffusion_models/flux1-dev-fp8.safetensors` | **11 GB** | FLUX dev，InfiniteYou 角色参考图需要 |
| `ComfyUI/models/diffusion_models/ltx-2.3-22b-distilled-Q6_K.gguf` | **16.5 GB** | LTX 2.3 (ComfyUI 版)，但生产用 ltx-desktop |
| `ComfyUI/models/text_encoders/t5xxl_fp8_e4m3fn_scaled.safetensors` | **4.8 GB** | FLUX 配套 T5 encoder |
| `ComfyUI/models/loras/donghua-illustriousXL_v01.safetensors` | **0.7 GB** | 国漫风格 LoRA |
| LTX Desktop 整体 | **106 GB** | 如果视频也全转云端(Seedance)可删 |

⚠️ **如果角色参考图也转云端(GPT Image 2 已支持)，则 ComfyUI 整个 121 GB 可以删除**
⚠️ **如果视频也全转云端(Seedance)，则 LTX Desktop 106 GB 可以删除**
⚠️ **极端情况下可回收 227 GB → 只保留 14 GB 代码+工具**

---

## 四、层级完成度评估

| 层级 | 功能 | 完成度 | 可封层? |
|------|------|--------|---------|
| **Layer 1 · 小说** | 原文 txt 存储 | ✅ 100% | ✅ 已封 |
| **Layer 2.1 · 分镜图** | GPT Image 2 生成 | ✅ 95% | ✅ 可封 |
| **Layer 2.2 · 视频** | LTX 2.3 / Seedance | ✅ 90% | ⚠️ 后端选型中 |
| **Layer 3 · 音频** | Qwen3 TTS | ✅ 85% | ⚠️ 功能完整但未大规模验证 |
| **Layer 4 · 后期** | 字幕/转场/拼接 | ✅ 80% | ⚠️ 待更多集成测试 |
| **Layer 5 · 引擎** | ComfyUI/LTX/TTS | ✅ 运行中 | ❌ 依赖本地 GPU |
| **Layer 6 · 服务** | 流水线/ToonFlow | ✅ 核心完成 | ⚠️ 可封核心 |
| **Layer 7 · 发布** | 飞书/YouTube | 🔨 70% | ❌ 还在开发 |

---

## 五、推荐模块拆分方案

### 包 A: 核心流水线 (Layer 6 — 大脑)
```
Layer_6_Services/
  pipelines/          # batch_pipeline.py, batch_video_segments.py, image_backends.py...
  config/             # config.yaml
  db/                 # ToonFlow SQLite
  tools/              # 辅助工具
```
- **端口**: ToonFlow 60000, one-api 3000
- **依赖**: Python 3.10+, requests, sqlite3
- **调用**: → 包B (图片) → 包C (视频) → 包D (语音)

### 包 B: GPT 图片生成 (已打包 ✅)
```
GPT_图片生成工具/
  CatGPT-Gateway/     # 浏览器自动化调 ChatGPT
  app.py              # Web GUI
```
- **端口**: GUI 5400, Gateway 8000
- **被调用**: 包A POST → /v1/images/generations

### 包 C: 视频生成
```
选项 C1: LTX 2.3 Desktop (本地)  — 端口 3000, 106 GB
选项 C2: Dreamina 首尾帧 (云端)  — 端口 5300, 已打包 ✅
选项 C3: 两者并存 (生产用LTX, 云端备选Dreamina)
```

### 包 D: 语音生成
```
Qwen3_TTS/
  qwen3-tts 模型 (9 GB)
  TTS 服务 API
```

### 包 E: DPM 账号管理 (已打包 ✅)
```
Dreamina Pro Max 破解版/
  注册/管理 Dreamina 账号
  → 账号文件提供给 包C2 使用
```

### 互联架构
```
     包A (核心流水线)
     ↓ HTTP API 调用 ↓
  ┌──────┬──────┬──────┐
  包B    包C    包D    包E
  GPT图片 视频   TTS   账号
  :8000  :3000  :?    :5200
         :5300
```

**关键: 各包通过 HTTP API 通信，config.yaml 中配 URL 即可串联**

---

## 六、建议行动清单

### 立即可做 (回收 ~76 GB)
1. [ ] 删除 qwen_edit 模型 (38+8.7+8+0.8 = 55.5 GB)
2. [ ] 删除 flux-2-klein 模型 (8.8+0.3 = 9.1 GB)
3. [ ] 删除 AI_Tools 下废弃的即梦 API 工具 (~272 MB)
4. [ ] 删除 dreamina_auto_profiles + dreamina_browser_profile (~177 MB)
5. [ ] 删除 scripts/_archive + test_output + _tmp_scripts (~257 MB)
6. [ ] 删除 ComfyUI/output 旧生成图 (~670 MB)

### 评估后决定
7. [ ] 是否保留 ComfyUI/FLUX (角色参考图)? 如果 GPT Image 2 参考图够用 → 删除 121 GB
8. [ ] 是否保留 LTX Desktop (本地视频)? 如果全转 Seedance 云端 → 删除 106 GB
9. [ ] 是否保留 Qwen3 TTS (本地语音)? 如果转云端 TTS → 删除 9 GB

### 封层
10. [ ] Layer 1 (小说) → 打包封存
11. [ ] Layer 6 核心 → 锁定 API 接口，版本化

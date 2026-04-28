# 视频项目 - AI接入指南 (统一版)

## 项目架构 (6层生产 + 服务层)

```
视频项目/
├── Layer_1_Novel/          ← 内容层 (小说/脚本/大纲)
│   ├── seeds/              种子提示词
│   ├── templates/          模板
│   ├── output/             生成的小说
│   └── scripts/            脚本草稿/最终
│
├── Layer_2.1_Images/       ← 视觉层 (图片)
│   ├── raw/                原始生成
│   ├── edited/             编辑后
│   ├── upscaled/           超分后
│   └── storyboards/        分镜
│
├── Layer_2.2_Video/        ← 视觉层 (视频)
│   ├── raw/                原始生成
│   ├── edited/             编辑后
│   └── temp/               临时文件
│
├── Layer_3_Audio/          ← 音频层
│   ├── tts/                语音合成
│   ├── bgm/                背景音乐
│   ├── sfx/                音效
│   └── mix/                混音
│
├── Layer_4_PostProd/       ← 后期层
│   ├── subtitled/          字幕烧录
│   ├── merged/             音视频合并
│   └── upscaled/           超分输出
│
├── Layer_7_Publish/        ← 发布层
│   ├── thumbnails/         缩略图
│   ├── upload_ready/       待发布
│   └── published/          已发布
│
├── Layer_5_Engines/        ← 引擎层 (~116GB)
│   ├── comfyui/            图片生成 (ComfyUI)
│   ├── video/              视频生成
│   │   ├── ltx-desktop/    LTX Desktop (109GB)
│   │   ├── ltx-api/        LTX API
│   │   ├── lightx2v/       LightX2V (空/待安装)
│   │   └── toonflow/       ToonFlow
│   ├── audio/              语音合成
│   │   └── qwen3-tts/      Qwen3-TTS (~9GB)
│   ├── novel/              小说生成
│   │   └── AI_NovelGenerator/
│   └── tools/              工具
│       ├── ffmpeg/         视频处理
│       ├── one-api/        API聚合
│       └── social-auto-upload/ 自动发布
│
├── Layer_6_Services/       ← 服务层
│   ├── mcp/
│   │   ├── adapters/       MCP适配器 (9个)
│   │   └── servers/        MCP服务器
│   ├── pipelines/          流水线脚本
│   ├── config/             配置文件
│   ├── db/                 数据库
│   ├── logs/               日志
│   │   └── quality/        质量日志
│   ├── state/              状态文件
│   ├── cache/              缓存
│   ├── tools/              通用工具脚本
│   │   ├── check/          检查脚本
│   │   ├── fix/            修复脚本
│   │   └── setup/          安装脚本
│   └── tests/              测试
│
└── docs/                   ← 文档
```

## 关键路径

| 用途 | 路径 |
|------|------|
| 项目根目录 | `e:\视频项目` |
| 核心流水线 | `Layer_6_Services\pipelines\batch_pipeline.py` |
| 小说生成器 | `Layer_5_Engines\novel\AI_NovelGenerator\generate_novel.py` |
| ComfyUI | `Layer_5_Engines\comfyui\ComfyUI` |
| 图片适配器 | `Layer_6_Services\mcp\adapters\image_generator_adapter.py` |
| 风格配置 | `Layer_6_Services\config\style_profiles.json` |
| 输出小说 | `Layer_1_Novel\output` |
| 输出图片 | `Layer_2.1_Images\raw` |
| 输出视频 | `Layer_2.2_Video\raw` |
| 最终成品 | `Layer_7_Publish\upload_ready` |

## 工具启动方式

| 工具 | 启动命令 | 默认端口 |
|------|---------|---------|
| ComfyUI | `cd Layer_5_Engines\comfyui\ComfyUI && .venv\Scripts\python main.py` | 8188 |
| LTX Desktop | `Layer_5_Engines\video\ltx-desktop\LTXVideoDesktop.exe` | 8000 |
| ToonFlow | `Layer_5_Engines\video\toonflow\ToonFlow.exe` | 60000 |
| Qwen3-TTS | `cd Layer_5_Engines\audio\qwen3-tts && python webui.py` | 7860 |
| one-api | `Layer_5_Engines\tools\one-api\one-api.exe` | 3000 |

## 工作流

```
1. 小说生成 (Layer_5_Engines/novel)
   ↓
2. 风格配置 (Layer_6_Services/config)
   ↓
3. 图片生成 (Layer_5_Engines/comfyui) → Layer_2.1_Images/raw
   ↓
4. 视频生成 (Layer_5_Engines/video/ltx-desktop) → Layer_2.2_Video/raw
   ↓
5. 语音合成 (Layer_5_Engines/audio/qwen3-tts) → Layer_3_Audio/tts
   ↓
6. 后期制作 (Layer_4_PostProd/)
   ↓
7. 最终输出 (Layer_7_Publish/upload_ready)
   ↓
8. 自动发布 (Layer_5_Engines/tools/social-auto-upload)
```

## 风格配置

当前风格: **3D国漫 (Xianxia fantasy)**

配置文件: `Layer_6_Services\config\style_profiles.json` (projects["3"])

## MCP适配器 (9个)

| 适配器 | 功能 |
|--------|------|
| ImageGeneratorAdapter | 图片生成 |
| VideoGeneratorAdapter | 视频生成 |
| NovelGeneratorAdapter | 小说生成 |
| TTSAdapter | 语音合成 |
| PostprocessAdapter | 后期制作 |
| PublishAdapter | 自动发布 |
| PipelineAdapter | 流水线状态 |
| QualityAuditorAdapter | 质量审核 |
| DBAdapter | 数据库 |

## 注意事项

1. **LightX2V**: 目录为空，需要重新安装
2. **ComfyUI**: 符号链接到E:\ComfyUI，需要安装依赖和模型
3. **风格锁定**: 已解除，当前使用3D国漫风格
4. **ToonFlow回填**: 已实现，生成视频后自动记录状态

## 快速开始

```powershell
# 1. 启动所有服务
.\start_all_services.bat

# 2. 运行3D国漫风格流水线
.\start_3d_guoman.bat

# 3. 或手动运行
python Layer_6_Services\pipelines\batch_pipeline.py --style 3d_guoman --project 3
```

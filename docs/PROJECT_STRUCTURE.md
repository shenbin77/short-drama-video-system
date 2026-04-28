# 项目目录结构

> Last updated: 2026-04-21
>
> 本文档只回答一个问题：**各目录/模块在哪里、负责什么。**
>
> - 不定义运行入口（那是 `CURRENT_ENTRYPOINTS_RUNBOOK.md` 的职责）
> - 不定义多集群计划（那是 `planning/*.md` 的职责）
> - 如有冲突，以 `PROJECT_GUIDE.md` 为准

---

## 目录结构

```
e:\视频项目\
│
├── Layer_1_Novel/              ← 内容层 (小说/脚本/大纲)
│   ├── seeds/                  种子提示词
│   ├── templates/              模板
│   ├── output/                 生成的小说
│   └── scripts/                脚本草稿/最终
│
├── Layer_2.1_Images/           ← 视觉层 (图片)
│   ├── raw/                    原始生成
│   ├── edited/                 编辑后
│   ├── upscaled/               超分后
│   └── storyboards/            分镜
│
├── Layer_2.2_Video/            ← 视觉层 (视频)
│   ├── raw/                    原始生成
│   ├── edited/                 编辑后
│   └── temp/                   临时文件
│
├── Layer_3_Audio/              ← 音频层
│   ├── tts/                    语音合成
│   ├── bgm/                    背景音乐
│   ├── sfx/                    音效
│   └── mix/                    混音
│
├── Layer_4_PostProd/           ← 后期层
│   ├── subtitled/              字幕烧录
│   ├── merged/                 音视频合并
│   └── upscaled/               超分输出
│
├── Layer_5_Engines/            ← 引擎层 (~116GB)
│   ├── comfyui/                图片生成 (ComfyUI + Klein)
│   ├── video/                  视频生成
│   │   ├── ltx-desktop/        LTX Desktop (109GB)
│   │   ├── ltx-api/            LTX API
│   │   ├── lightx2v/           LightX2V 加速框架
│   │   └── toonflow/           ToonFlow
│   ├── audio/                  语音合成
│   │   └── qwen3-tts/          Qwen3-TTS (~9GB)
│   ├── novel/                  小说生成
│   │   └── AI_NovelGenerator/
│   └── tools/                  工具
│       ├── ffmpeg/             视频处理
│       ├── one-api/            API聚合
│       └── social-auto-upload/ 自动发布
│
├── Layer_6_Services/           ← 服务层
│   ├── mcp/                    MCP 服务
│   │   ├── adapters/           MCP 适配器 (9个)
│   │   └── servers/            MCP 服务器
│   ├── pipelines/              流水线脚本
│   │   └── batch_pipeline.py   核心 10 步流水线
│   ├── config/                 配置文件
│   │   ├── style_profiles.json 风格配置
│   │   ├── characters.json     角色定义
│   │   └── accounts_matrix.json 账号矩阵
│   ├── db/                     数据库
│   ├── logs/                   日志
│   ├── state/                  运行状态
│   ├── cache/                  缓存
│   ├── tools/                  运维脚本
│   │   ├── check/              检查脚本
│   │   ├── fix/                修复脚本
│   │   └── setup/              安装脚本
│   └── tests/                  测试
│
├── Layer_7_Publish/            ← 发布层
│   ├── thumbnails/             缩略图
│   ├── upload_ready/           待发布
│   └── published/              已发布
│
├── docs/                       ← 文档
│   ├── README.md               文档索引
│   ├── CURRENT_ENTRYPOINTS_RUNBOOK.md  运行入口说明
│   ├── PROJECT_STRUCTURE.md    本文档
│   └── planning/               规划文档
│
├── PROJECT_GUIDE.md            ← AI 接入指南 (权威)
├── README.md                   ← 项目首页
├── .env.example                ← 环境变量模板
└── requirements.txt            ← Python 依赖
```

---

## 生产配置

- **图片引擎**: FLUX2 Klein 9B FP8 + ReferenceLatent + Consistency LoRA V2
- **视频引擎**: LTX 2.3 Desktop (port 3000, 720p, 24fps, 5s)
- **加速框架**: LightX2V (FP8 量化, ⚠️ 待测试)
- **风格**: 3D 国漫暗黑东方玄幻 (Project 3: 禁蛊录)
- **GPU**: RTX 3090 24GB (串行: ComfyUI → 释放 → LTX)

---

## GPU 显存管理

24GB 总显存。ComfyUI 需 ~10GB，LTX 需 ~12GB，**不可同时运行**。

---

## 运行入口

详见 `CURRENT_ENTRYPOINTS_RUNBOOK.md`。

本文档不定义运行入口。

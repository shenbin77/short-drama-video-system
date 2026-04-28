# 禁蛊录 视频项目 — 实时状态看板

> **最后更新**: 2026-04-27 13:38
> **当前主线**: 4 工厂云端产线
> **Dreamina 策略**: 使用现有破解版/现有工作流，不安装 v7.1 新包

---

## 一、当前完成状态

| 阶段 | 状态 | 结果 | 位置 |
|------|------|------|------|
| 4 工厂架构 | ✅ 完成 | novel/storyboard/video/assembly 已创建 | `services/` |
| 小说工厂 | ✅ 完成首批 | 《禁蛊录》前 10 章已生成 | `services/novel-factory/output/` |
| 小说队列 | ✅ 完成首批 | 10 个章节 JSON 已入队 | `services/novel-factory/queue/` |
| 分镜工厂 | ✅ 第1集完成 | Episode 001 已生成 7 张分镜图，CatGPT 登录态失效时 GRSAI fallback 可用 | `services/storyboard-factory/output/episode_001/` |
| 视频工厂 | ✅ 第1集完成 | Dreamina 入口异常已暂停；已启用 FFmpeg Ken Burns 本地兜底，Episode 001 生成 6/6 段 | `services/video-factory/output/episode_001/` |
| 合成工厂 | ✅ 第1集完成 | FFmpeg + edge-TTS 已输出带配音成品 | `services/assembly-factory/output/禁蛊录_第001集.mp4` |
| 主调度器 | ✅ 已创建 | 一键启动 24h 产线 | `services/orchestrator.py` |
| 目录整理 | ✅ 第一轮完成 | v7.1 归档、测试脚本归档、中文输出说明 | `_packages/`, `_archive/`, `services/目录说明.md` |

---

## 二、重要决策

### Dreamina

不安装新包，不覆盖现有环境。

当前生产保留：

- `即梦/Dreamina Pro Max/`
- `打包发布/Dreamina_国际版_首尾帧工具/`
- `打包发布/Dreamina_Pro_Max_v7.0_破解版.zip`

新收到 v7.1 包已归档：

- `_packages/Dreamina/v7.1_customer_delivery/`

### DeepSeek

- V3 已下线。
- 小说使用 `deepseek-reasoner`，实际为 V4-Flash 推理模式。
- 前 10 章已跑通，总 tokens 约 38363。

### Qwen3-TTS

- 属于合成工厂。
- 合成工厂优先检测本地 `http://127.0.0.1:9880/health`。
- 不可用时自动 fallback 到 edge-TTS。

---

## 三、下一步

1. [x] 复测分镜工厂图片生成：CatGPT 优先，GRSAI fallback。
2. [x] 确认 Episode 001 分镜图成功生成，共 7 张。
3. [ ] 启动视频工厂，使用现有 Dreamina 首尾帧工具生成片段。
4. 启动合成工厂输出第 1 集成品。
5. 稳定后开启 orchestrator 24h 守护模式。

---

## 四、启动命令

```powershell
python E:\视频项目\services\novel-factory\novel_factory.py --chapters 10
python E:\视频项目\services\storyboard-factory\storyboard_factory.py --chapter 1
python E:\视频项目\services\video-factory\video_factory.py --episode 1
python E:\视频项目\services\assembly-factory\assembly_factory.py --episode 1 --tts auto
```

一键启动：

```bat
E:\视频项目\start_24h_production.bat
```


## 五、2026-04-27 重要风险与处理

- Dreamina 当前页面显示 Coming up soon，原 Seedance 免费入口暂不可用。
- 已创建 DISABLE_DREAMINA.flag，避免继续消耗 208 个账号池。
- 视频工厂已接入 Ken Burns 本地兜底，保证流水线不断。
- Episode 001 已完成：7 张分镜图、6 段兜底视频、edge-TTS 配音、最终 MP4。


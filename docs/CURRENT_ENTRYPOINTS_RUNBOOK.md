# 当前入口与运行说明

> Last updated: 2026-04-20
>
> 本文档只回答一件事：**现在这个项目到底应该从哪里启动、各入口负责什么、哪些脚本只是辅助工具。**
>
> 如果你对当前代码状态感到混乱，优先看这份文档，不要先去翻零散脚本。
>
> 本文档的职责边界：
>
> - **它负责定义当前运行入口与调试入口**
> - **它不负责定义项目目录结构**（那是 `PROJECT_STRUCTURE.md` 的职责）
> - **它不负责定义架构指南**（那是 `PROJECT_GUIDE.md` 的职责）
>
> 如果你是第一次进入这个项目，建议阅读顺序：
>
> 1. `docs/README.md`
> 2. `CURRENT_ENTRYPOINTS_RUNBOOK.md`
> 3. `PROJECT_STRUCTURE.md`

---

## 1. 当前唯一推荐主入口

### `Layer_6_Services/tools/run_pipeline.ps1`

这是**当前唯一推荐的日常运行入口**。

它负责：

- 启动章节级流水线编排
- 调用 `Layer_6_Services/pipelines/batch_pipeline.py` 跑主链
- 在图片阶段后自动读取 runtime state
- 自动解析真实 `script_id`
- 把真实 `script_id` 继续传给视频、TTS、后续步骤

适用场景：

- 正常跑一章
- 图片 + 视频 + 字幕 + 配音完整生产
- 已有一部分产物时断点续跑
- 不想手动拼 `script_id` / `outline_id`

### 推荐用法

```powershell
.\Layer_6_Services\tools\run_pipeline.ps1 -Chapter 1 -Title "除名"
```

如果只是常规生产，**优先跑这个，不要先手动拼多个 Python 脚本。**

---

## 2. 主链核心实现

### `Layer_6_Services/pipelines/batch_pipeline.py`

这是**核心 10 步流水线实现**，不是最优先的人类入口，但它是主链真正的执行主体。

负责：

- Step 0: 导入小说
- Step 1: 大纲
- Step 2: 创建剧本记录
- Step 3: 生成剧本
- Step 4-6: 分镜 / 资产 / 分镜图
- Step 7-10: 视频 / 字幕 / TTS / 超分
- 记录 runtime state

### 什么时候直接用它

只在下面情况直接调用：

- 你要调试某个 Step
- 你要从某个 Step 手动恢复
- 你要验证主链内部逻辑，而不是走 PowerShell 编排

### 示例

```powershell
python Layer_6_Services/pipelines/batch_pipeline.py --chapter 1 --title "除名" --novel Layer_1_Novel\个人_禁蛊录\ch1.txt
```

断点续跑示例：

```powershell
python Layer_6_Services/pipelines/batch_pipeline.py --chapter 1 --title "除名" --from-step 4 --script-id 12 --outline-id 12
```

---

## 3. 视频层专用入口

### `Layer_6_Services/pipelines/batch_video_segments.py`

这是**视频片段层专用入口**。

负责：

- 基于已有分镜图生成视频片段
- 合并片段
- 支持定向重跑坏镜头
- 支持视频层参数调整

### 什么时候用它

只在下面场景直接运行：

- 图片已经生成好了，只重跑视频层
- 只修几个坏片段
- 你在调试 `local_video_backends.py` 或视频 prompt 重写逻辑

### 示例

```powershell
python Layer_6_Services/pipelines/batch_video_segments.py --script-id 12 --duration 4
```

只重跑部分镜头：

```powershell
python Layer_6_Services/pipelines/batch_video_segments.py --script-id 12 --shot-indexes 3,7,9 --skip-clean
```

---

## 4. 兼容入口，不再推荐作为主入口

### `Layer_6_Services/pipelines/chapter_full_auto.py`

这个文件现在仍可运行，但**它已经不应该被视为第一主入口**。

当前定位：

- 历史兼容入口
- 单章快速试验入口
- 某些旧链路的兼容壳层

当前状态：

- 已接入 runtime state
- `script_id` / `outline_id` 缺省时可自动解析
- 默认视频时长、镜头数已尽量与主链对齐

### 为什么不建议把它当主入口

因为当前项目的编排逻辑已经逐步向这条路径收敛：

`run_pipeline.ps1` → `batch_pipeline.py` → runtime state → 下游步骤

所以 `chapter_full_auto.py` 更适合：

- 补兼容
- 临时试验
- 老流程过渡

而不是新的标准启动方式。

---

## 5. 当前运行时状态的唯一可信来源

### runtime state

统一状态目录：

```text
Layer_6_Services/logs/quality/runtime_state/
```

关键文件：

- `Layer_6_Services/logs/quality/runtime_state/latest_pipeline_state.json`
- `Layer_6_Services/logs/quality/runtime_state/chapter_{chapter}_{title}.json`

这些文件记录：

- `chapter`
- `title`
- `outline_id`
- `script_id`
- 当前执行步
- 状态 `running/completed`
- 当前配置路径

### 你应该怎么用它

如果你怀疑：

- 现在到底跑到第几步
- 这次真正的 `script_id` 是多少
- 下游视频/TTS 应该接哪个脚本 ID

**优先看 runtime state，而不是凭命令行记忆。**

---

## 6. 当前配置与路径的统一规则

### 配置统一入口

统一路径层：

- `Layer_6_Services/pipelines/runtime_paths.py`

统一读取对象：

- `config.yaml`
- `characters.json`
- `accounts_matrix.json`
- `Layer_6_Services/logs/quality/`
- `logs/`
- `output/`
- `runtime_state/`

### 当前规则

如果你后面继续开发，新的代码应优先通过 `runtime_paths.py` 读取：

- 配置文件
- 角色文件
- 质量日志目录
- 输出目录
- runtime state 路径

**不要再在新代码里手写：**

- `PROJECT_ROOT / "config.yaml"`
- `PROJECT_ROOT / "characters.json"`
- `os.path.join(root, "quality_logs", ...)`

除非这是一次性的独立脚本，而且你明确接受它不纳入主链统一治理。

---

## 7. 辅助工具脚本分类

下面这些脚本仍然有价值，但它们不是主入口。

### 质量与审计

- `Layer_6_Services/pipelines/storyboard_checker.py`
- `Layer_6_Services/pipelines/video_quality_review.py`
- `Layer_6_Services/pipelines/quality_feedback.py`
- `Layer_6_Services/pipelines/auto_video_optimize.py`
- `Layer_6_Services/tools/check/_full_audit.py`
- `Layer_6_Services/tools/check/video_readiness_check.py`

用途：

- 分镜抽查
- 视频质量抽查
- prompt 修复建议
- 自动重跑坏片段
- 全链路体检
- 视频层 readiness 检查

### 监管与运维

- `Layer_6_Services/pipelines/hermes_agent.py`
- `Layer_6_Services/pipelines/feishu_notify.py`
- `Layer_6_Services/tools/watchdog.ps1`

用途：

- 服务心跳
- 预算与质量预警
- 飞书通知
- 运维巡检

### 发布与分发

- `Layer_6_Services/pipelines/publish_chapter.py`
- `Layer_6_Services/pipelines/youtube_upload.py`

用途：

- 成片发布
- 平台上传
- 元数据生成

这些脚本属于：

- 诊断工具
- 运维工具
- 分发工具

**不要把它们和主生产入口混为一谈。**

---

## 8. 当前最推荐的工作流

### 日常生产

```text
run_pipeline.ps1
  → batch_pipeline.py
  → runtime_state 写入
  → 下游视频/TTS 继续读取真实 script_id
```

### 调试主链内部步骤

```text
batch_pipeline.py
  → 指定 from-step / to-step
```

### 只修视频层

```text
batch_video_segments.py
  → 指定 script-id
  → 必要时指定 shot-indexes
```

### 查状态

```text
Layer_6_Services/logs/quality/runtime_state/latest_pipeline_state.json
```

### 查质量问题

```text
Layer_6_Services/logs/quality/
```

---

## 9. 给当前项目的简单判断

截至 2026-04-20：

- **Phase 0 已完成**
  - runtime paths
  - runtime state
  - `script_id` 传递修复

- **Phase 1 已基本收口**
  - 主链核心入口已经收敛
  - 辅助脚本大部分已接统一路径层
  - 项目不再处于“完全不知道该从哪跑”的状态

- **Phase 2-5 尚未正式展开**
  - prompt polish 自动魔法棒
  - ToonFlow 面板化
  - 稳定性/发布/运营闭环增强

---

## 10. 一句话结论

如果你现在只记一条：

> **日常运行只优先用 `Layer_6_Services/tools/run_pipeline.ps1`；调试主链用 `Layer_6_Services/pipelines/batch_pipeline.py`；只修视频层用 `Layer_6_Services/pipelines/batch_video_segments.py`；查真实状态看 `Layer_6_Services/logs/quality/runtime_state/`。**

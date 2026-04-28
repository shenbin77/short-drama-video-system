# AI 短剧全自动生成系统 V13.0 — 6 工厂全自动产线

> 项目目标：把小说《禁蛊录》自动生产为竖版短剧视频。
>
> 当前主线：**小说工厂 → 图片工厂 → 视频工厂 → 音频合成工厂 → 发布工厂 → 运营工厂**。

---

## 1. 当前生产架构

```text
DeepSeek V4-Flash(reasoner)
        │
        ▼
📖 小说工厂 (工厂/01_小说工厂/)
输出: 章节正文 → queue/chapter_XXX.json
        │
        ▼
🎨 图片工厂 (工厂/02_图片工厂/)
GPT Image 2: CatGPT-Gateway(主) → GRSAI中转(备)
并行生成 max_workers=2，outline.json缓存，prompt安全审计
输出: episode_XXX/ 7张分镜图 → 视频工厂/queue/
        │
        ▼
🎬 视频工厂 (工厂/03_视频工厂/)
Dreamina Seedance 首尾帧，208个账号池
输出: segment_XX.mp4 → 音频合成工厂/queue/
        │
        ▼
🎥 音频合成工厂 (工厂/04_音频合成工厂/)
Qwen3-TTS(主) / edge-TTS(备) + FFmpeg合成
输出: 禁蛊录_第XXX集.mp4 → 发布工厂/upload_ready/
        │
        ▼
📤 发布工厂 / 运营工厂
```

---

## 2. 核心目录（当前实际结构）

```text
E:\视频项目\
├── README.md
├── start_all_services.bat            # 一键启动 one-api + CatGPT-Gateway + Qwen3-TTS
├── start_24h_production.bat          # 一键启动全流水线 (orchestrator)
│
├── 工厂\
│   ├── 01_小说工厂\                  # novel_factory.py + AI_NovelGenerator/ + one-api/
│   ├── 02_图片工厂\                  # storyboard_factory.py + CatGPT-Gateway/ + 资产库/ + pipelines/
│   │   ├── pipelines\image_backends.py  # CatGPT(主) + GRSAI(备) 生图后端
│   │   ├── 资产库\asset_manager.py      # 角色参考图管理
│   │   └── output\{novel}\episode_XXX\  # 分镜图输出
│   ├── 03_视频工厂\                  # video_factory.py + Dreamina/ + 即梦/
│   ├── 04_音频合成工厂\              # assembly_factory.py + qwen3-tts/ + tools/ffmpeg/
│   ├── 05_发布工厂\                  # social-auto-upload/ + upload_ready/ + published/
│   ├── 06_运营工厂\                  # ops_factory.py + stats/ + reports/
│   └── orchestrator.py
│
├── config\                           # api_keys.json, config.yaml, characters.json
├── data\                             # 运行数据
├── docs\                             # 文档
└── _archive\                         # 历史归档
```

---

## 3. 图片工厂 — 技术细节

### 生图后端优先级
```text
CatGPT-Gateway (http://127.0.0.1:8800)
  └─ HTTP 500 → 立即 fallback（不重试）
  └─ HTTP 422 参考图被拒 → 去掉参考图纯文本重试
  └─ 3次全失败 → 自动切 GRSAI

GRSAI 中转 (https://grsai.dakka.com.cn)
  └─ SSL断连 → Retry adapter 自动重试
  └─ 主域名失败 → 自动切备用域名(grsaiapi.com)
  └─ output_moderation → 触发 SAFE_FALLBACK_PROMPT 降级
```

### 代理设置（关键）
CatGPT-Gateway 使用 `socks5://127.0.0.1:7890` (Clash)。
**必须将 Clash 切换到 ChatGPT 专用节点：**
- 推荐：新加坡01|ChatGPT (188ms) 或 日本01|ChatGPT (218ms)
- 不能用 Netflix 节点（ChatGPT 被封）

### 并行生成
`ThreadPoolExecutor(max_workers=2)` — 2张图并行生成，效率提升约50%。
- 线程1：CatGPT → GRSAI fallback
- 线程2：CatGPT → GRSAI fallback
- 总积分消耗：每次失败后GRSAI消耗约150积分/张

### Prompt 安全
1. `sanitize_prompt()` — 关键词替换（血腥/暴力/监禁 → 安全表达）
2. `audit_image_prompt()` — 检测到触发词时 DeepSeek 重写
3. `SAFE_FALLBACK_PROMPT` — GRSAI output_moderation 时降级为氛围镜头

### Outline 缓存
每章首次生成时，outline 写入 `output/{novel}/episode_XXX/outline.json`。
重跑时自动读取，保证分镜词不变。

---

## 4. 视频工厂 — Dreamina 账号管理

### 账号生命周期
- 每个账号注册时获得固定积分（Seedance 每次消耗约70积分）
- 用完即报废，标记进 `Dreamina/output/used_accounts.txt`
- **失败的账号也会被标记**（防止死循环重试）
- 208个账号池 → 约208个视频片段配额

### 24h 自动生号（账号池持续补充）
```bat
:: 保持此窗口24h运行，自动补充账号池
E:\视频项目\工厂\03_视频工厂\start_account_generator.bat
```
- 每批注册 **200 个账号（3线程并发）**，完成后 60 秒继续下一批
- 新注册账号写入 `registered_accounts_usa/accounts_YYYYMMDD_HHMMSS.txt`
- `video_factory.py` 启动时自动合并所有账号文件 → `_master_accounts.txt`
- **不需要 ChatGPT 专用代理节点**，普通美区节点即可

### 批次规模选择依据
| 参数 | 值 | 说明 |
|------|----|------|
| `--count` | 200 | 每批注册数，超过 300 有 IP 频率风险 |
| `--threads` | 3 | 并发浏览器数，**不要超过 3**（同 IP 多浏览器被 Cloudflare 检测） |
| 单批耗时 | ~1.5-2h | 200账号 ÷ 3线程 × 每次 1-2min |
| 24h 产能 | ~2000账号 | 10批 × 200个 |
| 实际有效率 | ~50% | 部分账号注册成功但 0 积分，只有带 Sessionid + 70积分的才可用 |
| 有效账号/天 | ~1000个 | 满足 1000÷6 ≈ **166 集/天** 的视频配额 |

### 并行策略
- 当前：串行，每次1个账号（安全，不被风控）
- 并行上限2个（同一 IP 多浏览器会被 Cloudflare 检测）

### Headless 模式
当前 `HEADLESS=1`（无窗口）运行。如遇登录失败，改为有窗口调试：
```powershell
python E:\视频项目\工厂\03_视频工厂\Dreamina\dreamina_first_last_batch.py
# 去掉 HEADLESS=1 环境变量显示浏览器窗口
```

---

## 5. 小说工厂 — 技术细节

- 模型: `deepseek-reasoner` (小说正文), `deepseek-chat` (大纲/审计)
- 风格: 爆款网文框架 + 角色卡 + 世界观 + 金手指约束
- 输出: `queue/chapter_XXX.json` (章节正文 + 元数据)
- 当前: 《禁蛊录》前10章已生成

---

## 6. 合成工厂 — 技术细节

### TTS 优先级
```text
Qwen3-TTS (http://127.0.0.1:5500)  ← start_all_services.bat 已启动
  └─ 不可用 → edge-TTS (Microsoft 免费, 默认男声 zh-CN-YunxiNeural)
```

### FFmpeg 合成流程
1. 合并所有视频片段 (`concat`)
2. TTS 生成每段旁白音频 → 拼接为整集配音
3. 混入配音到合并视频
4. 输出 `output/禁蛊录_第XXX集.mp4` → 推送到 `05_发布工厂/upload_ready/`

---

## 7. 一键启动

### 标准生产流程（推荐）

**第一步: 启动后端服务**（首次或重启后）
```bat
E:\视频项目\start_all_services.bat
```
启动: one-api + CatGPT-Gateway(8800) + Qwen3-TTS(5500)
> ⚠️ 启动 CatGPT 前先将 Clash 切到 **新加坡01|ChatGPT** 或 **日本01|ChatGPT**

**第二步: 24h 账号池补充**（视频工厂需要）
```bat
E:\视频项目\工厂\03_视频工厂\start_account_generator.bat
```
保持此窗口运行，持续注册 Dreamina 账号。不需要 ChatGPT 专用代理节点。

**第三步: 启动全流水线**
```bat
E:\视频项目\start_24h_production.bat
```

### 手动调试单工厂
```powershell
# 图片工厂 (指定章节)
python E:\视频项目\工厂\02_图片工厂\storyboard_factory.py --chapter 1

# 视频工厂 (指定集)
python E:\视频项目\工厂\03_视频工厂\video_factory.py --episode 1

# 合成工厂 (指定集)
python E:\视频项目\工厂\04_音频合成工厂\assembly_factory.py --episode 1

# 合成工厂 (强制用 edge-TTS，不依赖 Qwen3-TTS 服务)
python E:\视频项目\工厂\04_音频合成工厂\assembly_factory.py --tts edge
```

---

## 8. 完整变更记录

### 2026-04-27 V13.0 全工厂全面审计修复

#### 📌 跨工厂通用修复
| 文件 | Bug | 修复 |
|------|-----|------|
| storyboard_factory.py | NOVEL_NAME fallback 拼写错误 "禁辜录" | → "禁蛊录" |
| video_factory.py | NOVEL_NAME fallback 拼写错误 | 同上 |
| assembly_factory.py | NOVEL_NAME fallback 拼写错误 | 同上 |

#### 🎨 02_图片工厂 (image_backends.py)
- CatGPT **HTTP 500** → 立即 fallback，不重试（原来浪费3×30s）
- CatGPT **HTTP 422 参考图被拒** → 检测错误信息，去掉参考图纯文本重试
- CatGPT 请求超时 300s → **90s**
- GRSAI 添加 **Retry adapter** 解决 SSL 断连问题
- GRSAI **备用域名**自动切换 (grsai.dakka.com.cn → grsaiapi.com)
- GRSAI 最终图片下载改用 session（获得 SSL 重试保护）

#### 🎨 02_图片工厂 (storyboard_factory.py)
- 并行生图 `ThreadPoolExecutor(max_workers=2)` — 效率提升约 50%
- `outline.json` 缓存 — 重跑时分镜词不变
- **process_chapter**: video_queue 存在时验证图片真实性，半成品自动重新生成
- **main()**: pending 扫描也验证图片完整性（不再跳过不完整的包）
- 修复 `episode_dir.mkdir` 缺 `parents=True`（新小说首次运行必崩）
- 修复**双重 STYLE_PREFIX**（storyboard 外层 + image_backends 内层叠加）
- 修复 `ref_images` 总数上限 2 张
- attempt 0 返回 None 时补充日志

#### 🎬 03_视频工厂 (video_factory.py)
- **[关键]** `DREAMINA_FAST_COST="0"` → **"70"** — 之前筛选了0积分账号，导致永远无有效账号
- **[关键]** `DREAMINA_COUNTRY="Hong Kong"` → **""** — 所有账号是Taiwan，之前全被国家过滤
- **process_episode**: assembly 包存在时验证 segment 真实性，全失败时重新生成
- `assembly_data.status`: 真实反映成功/失败（原来永远是 "ready"）
- `find_accounts_file()`: 合并所有 `accounts_*.txt` → `_master_accounts.txt`，新注册账号自动可用
- 修复 `ep_dir.mkdir` 缺 `parents=True`
- `success_segments` 提前计算，避免 dict 自引用 bug
- **[关键]** `.mp4` 文件检测加时间戳过滤 — 避免旧残留视频被错误分配给新 segment

#### 🎬 03_视频工厂 (dreamina_first_last_batch.py)
- 失败账号**标记 used**（防止死循环重试）
- `joella6d0c39` 等反复失败账号加入 `SKIP_EMAILS`

#### 🎬 03_视频工厂 (新增)
- `start_account_generator.bat` — 24h 自动循环注册 Dreamina 账号

#### 🎥 04_音频合成工厂 (assembly_factory.py)
- **main()**: 跳过 `status=failed` 的集（视频全失败时不无限重试）

#### 🚀 start_all_services.bat
- 启动前提示切换到 ChatGPT 专用代理节点（新加坡/日本 ChatGPT 节点）
- 修正 CatGPT-Gateway 端口 8000 → **8800**

---

### 2026-04-27 V12.0 初始搭建
- 6 工厂产线搭建（小说/图片/视频/合成/发布/运营）
- 已生成《禁蛊录》前 10 章到图片工厂队列
- 归档 Dreamina v7.1（不安装不覆盖现有环境）
- CatGPT-Gateway + GRSAI 双后端集成
- DeepSeek prompt 审计 + 安全关键词替换

# Seedance 2.0 提示词指南 (首尾帧模式)

> 来源: 官方指南 + 社区总结。提示词质量直接决定画面质量。

## 核心公式 (6 步)

```
[主体], [动作], in [环境], camera [镜头运动], style [风格], avoid [约束]
```

### 首尾帧模式 (Image-to-Video) 专用公式

首尾帧模式下，画面内容已由图片定义，提示词只需描述**运动和变化**：

```
Animate the provided frames, preserve composition and colors,
[动作描述], camera [镜头运动], keep consistent lighting,
[时长] seconds, avoid [约束].
```

## 镜头运动 (8种)

| 类型 | 英文关键词 | 适用场景 |
|------|-----------|---------|
| 推进 | `slow push-in` | 聚焦角色面部/细节 |
| 拉远 | `pull-out / dolly out` | 展示全景/环境 |
| 横移 | `lateral tracking` | 跟随角色行走 |
| 升降 | `crane up / crane down` | 俯仰切换/揭示场景 |
| 环绕 | `orbit around` | 360°展示角色/物体 |
| 固定 | `static / locked frame` | 对话场景/特写 |
| 手持 | `handheld subtle sway` | 紧张/真实感 |
| 低角度 | `low tracking shot then subtle rise` | 战斗/力量感 |

### ⚠️ 镜头运动规则

1. **只用一个主镜头运动** — 多个冲突指令会导致抖动
2. **用节奏描述，不用技术参数** — `slow, smooth, stable` ✅ | `24fps, f/2.8` ❌
3. **镜头运动和角色运动分开写** — 不要混淆

## 速度关键词

| 节奏 | 关键词 |
|------|--------|
| 慢 | `slow, gentle, gradual` |
| 中 | `smooth, steady` |
| 快 | `dynamic` (⚠️ 慎用 fast，容易出瑕疵) |

> **⚠️ "fast" 是最容易降质的关键词！** 快镜头+快剪+复杂场景 = 必出瑕疵

## 光照 (画面质量第一杠杆)

如果只能加一个元素，加光照描述：

| 场景 | 光照描述 |
|------|---------|
| 白天室外 | `warm golden hour lighting` |
| 夜晚 | `moonlit, cool blue ambient` |
| 战斗 | `dramatic rim lighting, strong contrast` |
| 室内 | `soft window light, warm tone` |
| 仙侠 | `ethereal glow, volumetric god rays` |

## 3D 国漫风格模板

### 对话场景
```
Animate the provided frames, preserve 3D donghua style and colors,
[角色名] speaks with subtle facial expression and gentle gestures,
camera static with subtle push-in,
soft volumetric lighting, keep consistent character design,
5 seconds, 9:16, avoid jitter and style drift.
```

### 战斗场景
```
Animate the provided frames, preserve 3D donghua style,
[角色名] strikes with [weapon/technique], dramatic impact effects,
camera low tracking shot then subtle rise,
dramatic rim lighting with strong contrast,
5 seconds, 9:16, avoid bent limbs and temporal flicker.
```

### 景色/转场
```
Animate the provided frames, preserve composition and colors,
gentle wind motion through environment, floating particles,
camera slow lateral tracking,
golden hour cinematic lighting,
5 seconds, 9:16, avoid jitter.
```

### 情绪/特写
```
Animate the provided frames, preserve character identity,
[角色名] shows [emotion] with subtle eye and lip movement,
camera holds fixed framing with very slow push-in,
soft warm lighting on face,
5 seconds, 9:16, avoid identity drift and expression exaggeration.
```

## 避免的关键词

- ❌ `fast` (用 `dynamic` 替代)
- ❌ 多个镜头运动叠加
- ❌ 摄影技术参数 (fps/光圈/ISO)
- ❌ `text`, `subtitle`, `watermark`
- ❌ `blurry`, `low quality` (负面暗示反而引入瑕疵)

## 产出质量 Checklist

每条提示词提交前检查：
- [ ] 有明确动作描述？
- [ ] 有且只有一个镜头运动？
- [ ] 有光照描述？
- [ ] 速度关键词是 slow/smooth？(非 fast)
- [ ] 有 avoid 约束？(jitter, flicker, drift)
- [ ] 首尾帧模式下不重复描述画面内容？(图片已定义)

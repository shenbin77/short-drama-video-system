# 文档维护规则

> 目的：防止项目文档再次出现“重复解释、权威来源不清、历史文档与当前文档竞争”的问题。

---

## 1. 总原则

- 每个主题尽量只有一份权威文档
- 根 `README.md` 只做首页导航和当前状态摘要
- `docs/` 负责承接主题型说明
- `docs/planning/` 只负责计划、路线图、审计规则、集群协作
- 阶段性大文档可以保留，但必须明确标记为历史/深度参考

---

## 2. 当前权威分工

| 主题 | 权威文档 | 不应该由谁重复定义 |
|------|----------|--------------------|
| 项目首页/导航 | `README.md` | `README_v7.4_full.md`、各类 planning 文档 |
| docs 总索引 | `docs/README.md` | 根 README、历史文档 |
| 当前运行入口 | `docs/CURRENT_ENTRYPOINTS_RUNBOOK.md` | `PROJECT_STRUCTURE.md`、历史大文档、planning 文档 |
| 项目结构 | `docs/PROJECT_STRUCTURE.md` | runbook、历史大文档 |
| 多集群总计划 | `docs/planning/multi_cluster_development_plan.md` | framework guide、cluster README |
| 多集群框架用法 | `docs/planning/multi_cluster_framework_guide.md` | 根 README、运行入口文档 |
| 到100%路线 | `docs/planning/roadmap_to_100_percent.md` | 根 README、framework guide |
| 集群任务边界 | `clusters/dev/workspace/cluster_*/README.md` | 总 README、framework guide |

---

## 3. 写文档时的禁止事项

不要在非权威文档里重复写以下内容：

- 当前唯一推荐运行入口
- 完整启动命令全集
- 多集群总边界与合并闸门
- 项目目录全量说明
- 已废弃方案仍以现在时态描述为当前标准

如果确实需要提到，应该：

- 只写一句摘要
- 然后链接到权威文档

---

## 4. 历史文档规则

历史文档允许保留，但必须满足：

- 顶部明确写出“历史阶段沉淀 / 深度参考文档”
- 明确说明“不是当前唯一权威来源”
- 指向当前权威文档
- 不再承担首页、运行入口、规划边界的职责

---

## 5. 更新规则

当以下内容变化时，必须同步更新对应权威文档：

### 运行入口变化

更新：

- `docs/CURRENT_ENTRYPOINTS_RUNBOOK.md`
- 如有必要，再更新 `README.md` 的入口摘要

### 目录结构变化

更新：

- `docs/PROJECT_STRUCTURE.md`

### 多集群边界变化

更新：

- `docs/planning/multi_cluster_development_plan.md`
- 对应 `clusters/dev/workspace/cluster_*/README.md`

### 文档入口变化

更新：

- `docs/README.md`
- 必要时更新根 `README.md`

---

## 6. Kimi 多集群协作规则

Kimi 多集群如需新增文档，应优先判断属于哪一类：

- 运行说明
- 结构说明
- planning / 审计
- 模块任务书
- 历史记录

禁止出现：

- 同一个主题新增第二份“事实标准文档”
- 在 cluster README 中复制总计划表全文
- 在历史文档中继续追加当前标准说明

---

## 7. 最小实践

以后新增文档前，先问自己三个问题：

- 这份文档解决的是哪个单一问题？
- 这个主题是否已经有权威文档？
- 我应该新增文档，还是给现有权威文档加一节？

如果第二个问题的答案是“已经有”，优先更新旧文档，而不是新建一份重复说明。

---

## 8. 一句话原则

> 少写重复说明，多写权威链接；少造第二份标准，多维护第一份标准。

# TechPilot PROJECT_GOVERNANCE

> 本文是项目文档、每日开发流程和阶段收尾的固定规范。除非明确进行治理版本升级，否则不得随意改变文档职责和结构。

## 1. 文档职责

### `README.md`

用途：GitHub 首页，面向第一次访问仓库的人。

更新时机：完成一个 P 阶段，或项目定位、启动方式发生重大变化。

固定内容：

1. 项目定义
2. 核心能力
3. 当前状态
4. 技术栈
5. 架构概览
6. 快速启动
7. 评测结果
8. Roadmap

禁止写入：

- 每日开发流水
- 详细踩坑
- 临时状态
- 仅本地信息

### `PROJECT_CONTEXT.md`

用途：新对话的第一交接入口。

更新时机：每个 Day 结束。

长度要求：尽量保持一页以内。

固定结构：

1. 当前阶段
2. 当前工程状态
3. 当前能力
4. 当前风险
5. 下一步
6. 恢复顺序

禁止写入：

- 完整历史
- 长篇设计说明
- 详细命令
- 每日复盘

### `NEXT_SESSION_PROMPT.md`

用途：下一次新对话直接复制使用。

更新时机：每个 Day 结束。

必须包含：

- 必读文件顺序
- 当前阶段和已知状态
- 当日目标确认要求
- 引导式学习要求
- 当日收尾要求

### `docs/PROJECT_STATUS.md`

用途：记录整个项目做到哪里，以及可验证的完成证据。

更新时机：每个 Day 结束。

固定结构：

1. 当前版本
2. 当前阶段
3. 阶段状态
4. 已完成
5. 验收证据
6. 架构文档引用
7. 已知非阻塞问题
8. 下一步

禁止写入：

- 长篇架构图
- 详细操作命令
- 逐日踩坑过程

### `docs/ARCHITECTURE.md`

用途：描述系统结构、数据流、模块职责和关键边界。

更新时机：系统链路、组件关系或核心设计发生变化时。

固定结构：

1. 核心边界
2. 摄取链路
3. 检索链路
4. 问答链路
5. 删除链路
6. 评测链路
7. 未实现能力

禁止写入：

- 每日进度
- Git 操作
- 详细排障命令

### `docs/DEV_LOG.md`

用途：按时间记录完成内容、关键设计、验收、错误和修复。

更新时机：每个 Day 结束。

每个 Day 固定结构：

1. 完成
2. 关键设计
3. 验收
4. 错误与修复

只记录有长期价值的内容，不强制固定条数。

### `docs/RUNBOOK.md`

用途：启动、运行、测试、评测和排障操作手册。

更新时机：新增命令、验证方式或常见故障时。

固定结构：

1. 每天开始开发
2. 每天结束开发
3. 按 Day 或功能划分的操作步骤
4. 通用常见问题

禁止写入设计背景和开发历史。

### `docs/INTERVIEW_NOTES.md`

用途：沉淀可用于面试表达的设计问题和答案。

更新时机：出现新的重要设计、取舍、故障或评测结论时。

写作要求：

- 使用问题作为标题。
- 回答为什么这样设计。
- 必要时说明替代方案、优缺点和失败处理。
- 不重复粘贴代码。

### `docs/LEARNING_PROTOCOL.md`

用途：固定学习方式、协作方式和纠错规则。

更新时机：学习模式发生明确变化时。

禁止写入每日项目状态和具体实现记录。

### `.local/reviews/dayXX-review.md`

用途：个人每日复盘，只保留本地。

更新时机：每个 Day 结束。

固定结构：

1. 今日完成
2. 系统链路
3. 必须理解
4. 不要求背诵
5. 错误与修复
6. 面试问答
7. 明日第一任务

`.local/` 必须加入 `.gitignore`。

### `docs/product-baseline.md`

用途：固定产品定义、核心能力、第一版范围和禁止功能。

更新时机：正式批准产品范围变更时。

### `docs/milestones.md`

用途：记录 P0–P7 阶段范围及其状态。

更新时机：阶段开始、阶段完成或正式调整里程碑时。

## 2. 每日开始流程

1. 读取 `PROJECT_CONTEXT.md`。
2. 按其顺序读取项目文档和总控手册。
3. 执行 `git pull`。
4. 启动依赖并应用 Alembic Migration。
5. 确认健康检查。
6. 根据总控手册确认当天 Day、目标和验收标准。
7. 先讲清产品、数据和系统背景，再开始实现。

## 3. 每日开发流程

严格遵守：

1. 产品视角
2. 数据视角
3. 系统视角
4. 实现视角
5. 验证视角
6. 面试视角

除非用户明确要求，否则不得一次性替用户完成整个模块。优先引导其理解设计、阅读代码并完成局部实现。

## 4. 每日收尾流程

当天验收项完成后自动执行：

1. 对照总控手册检查验收项。
2. 运行自动化测试和关键真实验证。
3. 更新 `PROJECT_CONTEXT.md`。
4. 更新 `docs/PROJECT_STATUS.md`。
5. 更新 `docs/DEV_LOG.md`。
6. 必要时更新 `docs/RUNBOOK.md`。
7. 必要时更新 `docs/ARCHITECTURE.md`。
8. 更新 `docs/INTERVIEW_NOTES.md`。
9. 生成 `.local/reviews/dayXX-review.md`。
10. 更新 `NEXT_SESSION_PROMPT.md`。
11. 检查 `git diff --check`、`git status` 和关键 Diff。
12. Commit 并 Push。
13. 明确给出：PASS / CONDITIONAL PASS / FIX。

## 5. 阶段收尾流程

完成一个 P 阶段时，在每日收尾基础上额外执行：

1. 对照 Milestone 检查阶段范围。
2. 确认阶段级测试与评测证据。
3. 更新 `README.md`。
4. 更新 `docs/milestones.md`。
5. 更新 `docs/ARCHITECTURE.md`。
6. 检查 GitHub Milestone 状态。
7. 形成阶段结论并冻结文档基线。

## 6. 新对话恢复顺序

默认顺序：

1. `PROJECT_CONTEXT.md`
2. `docs/PROJECT_STATUS.md`
3. `docs/LEARNING_PROTOCOL.md`
4. `docs/PROJECT_GOVERNANCE.md`
5. `docs/ARCHITECTURE.md`
6. `docs/RUNBOOK.md`
7. `docs/DEV_LOG.md`
8. `docs/INTERVIEW_NOTES.md`
9. 《TechPilot 强大模型应用开发项目总控手册》
10. GitHub `main`

用户明确说明存在本地未 Push 修改时，以该说明和本地状态为准。

## 7. Git 规则

- `.env`、`.local/`、`eval/` 和本地评测结果不得提交。
- 提交前必须执行测试、`git diff --check` 和 `git status`。
- Commit Message 应说明本次功能或文档变化，不使用无意义描述。
- 未完成或未验证的功能不得标记为 PASS。
- 文档状态、代码状态和 GitHub 状态必须一致。

## 8. 变更控制

新增文档前必须确认：

1. 现有文档无法承载该职责。
2. 新文档具有单一职责。
3. 有明确更新时机。
4. 不与现有文档重复。

默认不再新增文档，也不随意改变固定结构。

<!-- LOCAL_ARTIFACT_LAYOUT_POLICY -->
## Local artifact layout policy

Repository-root local-only layout:

```text
TechPilot/
├── .local/
│   ├── days/
│   │   └── dayXX.../   # per-day evals, diagnostics, backups, temporary artifacts
│   └── reviews/        # day review documents
├── .pytest_cache/      # pytest-managed cache; sibling of .local, leave untouched
└── ...
```

Rules:
- Any new day-scoped TechPilot artifact MUST go under `.local/days/dayXX/`
  (or a descriptive sibling such as `.local/days/day15_overlay_backups/`).
- Do NOT create new top-level `.local/dayXX...` directories.
- `.local/reviews/` remains separate from `.local/days/`.
- Repo-root `.pytest_cache/` is pytest-managed and MUST remain outside `.local/`.
- `.local` is local-only and must not be committed.
- When code/docs reference a day artifact, use `.local/days/dayXX/...`.
- Future closeout/evaluation scripts must follow this layout automatically.

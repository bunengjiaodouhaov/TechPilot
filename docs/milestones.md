# TechPilot P0–P7 里程碑

> GitHub Milestones 用于管理八个阶段。功能范围以《TechPilot 强大模型应用开发项目总控手册》为准；不提前创建没有当前阶段需求的额外功能。

## P0：工程骨架（Day 1–3）

- 冻结产品范围
- FastAPI 最小工程
- PostgreSQL、Redis、Qdrant
- SQLAlchemy 与 Alembic
- Workspace、Document、Chunk
- 依赖健康检查
- 第一条文档数据链路

### 当前状态

- Day 1：完成
- Day 2：完成
- Day 3：完成
- P0：完成

## P1：文档 RAG（Day 4–11）

- 文档解析与 Chunk：完成
- Embedding / Dense Retrieval：完成
- 30 条 Retrieval Golden 与 Baseline：完成
- 可信回答与 Citation：完成
- 无答案拒答与删除隔离：完成
- P1 生命周期验证：完成
- answerable=true 质量复验：完成

### 当前状态

- Day 4–10：完成
- Day 11：完成
- P1 Gate：`FIX`
- P1 Milestone：保持未关闭；等待 P2 检索优化后复验
- 主要阻塞：生产 Dense Top-5 未稳定召回权威证据，导致 answer correctness / citation support 仅 4/7

## P2：高质量 RAG（Day 12–17）

- BM25：Day 12 完成
- Dense / BM25 同 Golden 对比：完成
- RRF Hybrid Retrieval：Day 13 下一步
- Reranker：待开始
- 无答案 / 回答质量回归：待开始
- 消融实验：进行中
- 冻结首个可投版本：待开始

### 当前状态

- P2：进行中
- Day 12 正式结果：
  - Dense Recall@5 0.700000 / MRR@5 0.500000
  - BM25 Recall@5 0.700000 / MRR@5 0.567778
  - Dense-only hit 4 / BM25-only hit 4 / both miss 5
- Day 13：RRF Hybrid Retrieval

## P3：Code RAG（Day 18–28）

- 仓库摄取
- Python AST
- 函数/类级代码切分
- Code Hybrid Retrieval
- 文件级引用与调用链
- Code RAG 评测

## P4：技术调研 Agent（Day 31–37）

- 查询澄清与规划
- 搜索工具与来源去重
- 文档/代码检索工具
- 证据验证
- 补充检索与终止条件
- Agent 评测

## P5：岗位与项目证据（Day 38–42）

- JD Structured Output
- 技能归一化
- 仓库能力证据检索
- 证据强弱判断

## P6：能力补齐 Agent（Day 43–46）

- 状态图
- 任务依赖与优先级
- 人工确认
- 重试与恢复
- Agent 成功率与 Trace

## P7：工程与发布（Day 47–50）

- Docker Compose 发布配置
- 可观测性与 SSE
- 性能测试
- Demo、架构图、实验报告
- v1.0 发布与复盘

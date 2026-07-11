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
- Day 3：待开始

## P1：文档 RAG（Day 4–10）
- 文档解析与 Chunk
- Embedding
- Dense Retrieval
- 30 条基础评测集
- Recall@5、MRR Baseline
- 引用回答与无答案样本

## P2：高质量 RAG（Day 12–17）
- BM25
- RRF Hybrid Retrieval
- Reranker
- 无答案拒答
- 消融实验与回归
- 冻结首个可投版本

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

# TechPilot PROJECT_STATUS

## 当前版本

v0.6-dev

## 当前阶段

P1：文档 RAG — Day 10 Gate Review 结论为 CONDITIONAL PASS

## 阶段状态

- Day 1：已完成
- Day 2：已完成
- Day 3：已完成
- Day 4–5：已完成
- Day 6：已完成
- Day 7：已完成
- Day 8：已完成
- Day 9：已完成
- Day 10：Gate Review 已完成，结论为 CONDITIONAL PASS

## 已完成

### Day 1：冻结项目

- 创建 GitHub 仓库并完成首次提交
- 提交产品范围基线
- 创建 P0–P7 GitHub Milestones
- 建立 FastAPI 最小工程
- 实现 `GET /health`
- 完成基础自动化测试

### Day 2：基础设施

- 使用 Docker Compose 启动 PostgreSQL、Redis、Qdrant
- 使用 Pydantic Settings 管理环境配置
- 建立 SQLAlchemy 异步数据库连接
- 初始化 Alembic
- 建立 Workspace、Document、Chunk 模型
- 实现 `GET /health/dependencies`
- 完成依赖健康检查

### Day 3：第一条数据链路

- 实现 `POST /documents/upload`
- 实现 Markdown Parser 与 PDF Parser
- 实现结构优先 Chunker
- 生成稳定 `chunk_id`
- 保存 Chunk JSONB Metadata
- 建立 Document 四态状态机
- 完成上传、解析、入库 E2E
- 通过 Swagger 上传 5 份真实技术文档
- 最终生成 179 个有效 Chunk

### Day 4–5：基础检索

- 接入 `intfloat/multilingual-e5-base`
- 固定向量维度为 768，使用归一化 Embedding
- 实现独立 `EmbeddingProvider`
- 实现 Qdrant `VectorRepository`
- 实现 Qdrant Collection 创建、Upsert 和 Workspace 过滤检索
- 实现 `IndexingService`
- 将文档摄取链路接入自动向量索引
- 实现 `DenseRetrievalService`
- 不使用 LangChain 一键封装核心检索链路
- 建立 30 条人工标注 Golden Dataset
- 实现可复现的 Dense Retrieval 评测脚本
- 计算并保存 Recall@5 与 MRR Baseline
- 失败案例自动写入本地 JSONL

### Day 6：可信问答

- 新增 Answer 与 Citation 数据契约
- 实现 Context Builder
- 实现 Context Enricher
- 实现 DeepSeek Provider
- 实现 AnswerService
- 将 Dense Retrieval 接入回答链路
- 根据检索结果回查 PostgreSQL Chunk 正文
- 实现 `POST /answers`
- 返回结构化 Citation
- 支持无证据拒答
- 完成真实 Answer E2E

### Day 7：回答质量评测与文档删除

- 为 Document 增加 `deleted_at` 软删除字段和 Alembic Migration
- 实现 `DELETE /documents/{document_id}`
- PostgreSQL 作为事实来源，先提交软删除，再 Best-effort 清理 Qdrant
- Dense Retrieval 和 Chunk 正文回查均排除已删除文档
- Qdrant 支持按 `workspace_id + document_id` 删除向量
- 新增 `DocumentService`
- 新增删除服务与删除 API 自动化测试
- 新增 `scripts/answer_eval.py`
- 通过真实 `AnswerService` 输出 JSONL 评测结果
- 发现并修复 Entity Scope Mismatch
- 强化 Evidence Sufficiency 与主体一致性规则
- 完成删除后回答评测：3/3 PASS

### Day 8：引用、页码与上下文回归

- 新增 Citation Traceability 回归测试
- 验证 Markdown 标题路径可传递至 Chunk、Prompt 与服务端 Citation
- 验证 PDF 页码可传递至跨页 Chunk、Prompt 与服务端 Citation
- 验证 Context Budget 排除的来源不能被 LLM 伪造引用
- 保持生产逻辑不变，仅补充跨层验收证据

### Day 9：P1 集成验证与 Gate 证据

- 新增完整文档 RAG 生命周期集成测试
- 验证上传、持久化、向量索引、检索、回答、引用、删除和删除后拒答
- Fake LLM 会检查真实 Prompt 中的 `SOURCE_1` 与上传证据，避免无条件返回掩盖链路故障
- 扩展回答评测汇总：运行错误、过度拒答、正确拒答、错误回答与错误回答率
- 新增回答评测汇总单元测试
- 建立基于当前 5 份有效文档、1153 个 Chunk 的 10 条困难无答案样本
- 完成真实 AnswerService 无答案评测：10/10 正确拒答，错误回答率 0%
- 修复异步生命周期测试后 SQLAlchemy 连接池跨事件循环影响后续健康检查的问题
- README、Milestone 和 P1 Gate 结论继续留待 Day 10

### Day 10：P1 Gate Review

- P1 Gate 结论：`CONDITIONAL PASS`
- 确认上传、索引、检索、回答、Citation、拒答、删除隔离和回归测试满足 P1 核心工程退出条件
- 确认 10 条困难无答案样本只能证明无答案安全性，不能替代有答案质量评测
- 将唯一条件限定为：补充包含正常样本和易混淆样本的 `answerable=true` 定量评测集
- 条件要求记录 answer correctness、citation support 和 over-refusal
- OCR 明确属于摄取增强，不作为 P1 条件
- Hybrid Retrieval 和 Reranker 明确属于 P2，不作为 P1 条件
- README 与现有项目文档已同步为 `CONDITIONAL PASS`
- P1 Milestone 和 PR #2 保持未关闭、未合并，等待条件证据

## Day 4–5 验收证据

- `python -m py_compile scripts/retrieval_eval.py`：PASS
- pytest：47 passed
- Qdrant Repository Smoke：PASS
- 真实文档向量索引：179 Points
- Golden Dataset：30 条
- Recall@5：0.866667
- MRR@5：0.627778
- 失败案例：4 条
- `eval/`：仅本地，不提交 GitHub

## Day 6 验收证据

- `POST /answers`：HTTP 200
- Workspace 校验：PASS
- Dense Retrieval：PASS
- PostgreSQL Chunk 正文回查：PASS
- Context Builder：PASS
- DeepSeek API：PASS
- Citation：PASS
- Refused：PASS
- Real End-to-End：PASS

## Day 7 验收证据

- Document Soft Delete：PASS
- Deleted-document Retrieval Isolation：PASS
- Best-effort Qdrant Cleanup：PASS
- Delete Service / API Tests：PASS
- Entity Scope Mismatch Regression：PASS
- Post-delete Answer Evaluation：3/3 PASS
- 完整自动化测试：119 passed
- 非阻塞警告：FastAPI TestClient 的 Starlette/httpx 弃用警告

## Day 8 验收证据

- Citation Traceability Tests：3 passed
- Markdown Heading Path Propagation：PASS
- PDF Page Range Propagation：PASS
- Omitted Source Citation Rejection：PASS
- Dependency Health：HTTP 200，PostgreSQL / Redis / Qdrant 全部正常
- 完整自动化测试：122 passed
- `git diff --check`：PASS
- 非阻塞警告：FastAPI TestClient 的 Starlette/httpx 弃用警告

## Day 9 验收证据

- P1 Lifecycle Integration：PASS
- Prompt Source Marker / Uploaded Evidence Check：PASS
- Upload -> Answer -> Citation -> Delete -> Refuse：PASS
- Answer Evaluation Summary Unit Tests：3 passed
- Unanswerable Dataset：10 条
- Correct Refusals：10/10
- Incorrect Answers：0/10
- Incorrect-answer Rate：0.000000
- Runtime Errors：0
- Full Test Suite：126 passed
- Dependency Health Repeated 3 Times：PASS
- `git diff --check`：PASS
- P1 Gate Decision：CONDITIONAL PASS

## P1 Gate 唯一未完成条件

- 建立包含正常样本与易混淆样本的 `answerable=true` 定量评测集
- 每条样本提供参考答案、期望来源和明确验收标准
- 记录 answer correctness
- 记录 Citation 是否直接支持全部关键结论
- 统计 over-refusal
- 保留失败样本并完成审查，再决定是否转为最终 `PASS`

## 架构文档

系统架构、数据流和关键设计边界统一维护在 `docs/ARCHITECTURE.md`。

## 已知非阻塞问题

- FastAPI TestClient 触发 Starlette/httpx 弃用警告。
- 上传文件当前会整体读入内存，尚未实现文件大小限制和流式处理。
- 当前仅支持 Markdown 与文本型 PDF。
- 扫描型 PDF 尚未支持 OCR。
- 重复上传目前允许生成新的 Document 记录。
- 当前使用字符数限制，不是真实 tokenizer token 数。
- Dense Retrieval 的 4 条失败案例保留在本地，后续阶段再分析，不阻塞当前验收。
- 首次回答会触发 Embedding 模型冷启动，请求耗时明显高于后续请求。
- 当前 Context Builder 采用 Top-K 上下文组织方式，尚未加入 Reranker。
- Qdrant 删除当前采用 Best-effort Cleanup，长期方案为 Outbox Pattern。
- 部分早期知识库文档可能已经过时。
- 当前 10 条回答评测均为无答案样本，因此只能证明无答案安全性，不能计算有答案样本的过度拒答率或定量回答质量。

其中 OCR、Hybrid Retrieval 和 Reranker 均不属于 P1 Gate 条件。当前唯一 Gate 条件是补齐并记录 `answerable=true` 定量质量证据。

## 下一步

补充包含正常样本与易混淆样本的 `answerable=true` 定量评测，记录 answer correctness、citation support 和 over-refusal。完成并审查该证据后，再决定是否将 P1 从 `CONDITIONAL PASS` 更新为最终 `PASS`；OCR、Hybrid Retrieval 和 Reranker 不进入该条件。

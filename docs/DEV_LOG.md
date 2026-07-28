# TechPilot DEV_LOG

## Day 1：冻结项目

### 完成

- FastAPI 初始化
- `GET /health` 正常
- Swagger 正常
- pytest：1 passed
- VS Code 与本地 Python 开发环境完成
- GitHub 仓库初始化并推送
- 产品范围基线已提交
- P0–P7 GitHub Milestones 已创建

---

## Day 2：基础设施

### 完成

- 安装并验证 Docker Desktop 与 Docker Compose
- 启动并验证 PostgreSQL、Redis、Qdrant
- 新增统一配置模块
- 新增 SQLAlchemy 异步连接模块
- 初始化 Alembic
- 设计并实现 Workspace、Document、Chunk
- 实现 `GET /health/dependencies`
- pytest：2 passed

---

## Day 3：第一条数据链路

### 完成

- 新增 Markdown 和 PDF Parser
- 建立统一解析 Schema
- 实现 ParserRouter
- 实现 StructureAwareChunker
- 新增稳定 `chunk_id`
- 新增 Chunk JSONB Metadata
- 实现 IngestionService
- 实现 FastAPI 文件上传接口
- 新增真实 E2E 验证脚本
- 自动化测试达到 17 passed

### 真实文档反馈

第一版切块策略：

- 总 Chunk：312
- heading-only：133
- 小于 50 字符：113

调整后：

- 总 Chunk：179
- heading-only：0
- 小于 50 字符：0

---

## Day 4–5：基础检索

### 完成

- 配置 `intfloat/multilingual-e5-base`
- 实现 `EmbeddingProvider` 与 Sentence Transformers 适配器
- 对 Document 使用 `passage:` 前缀，对 Query 使用 `query:` 前缀
- 固定 768 维并进行向量数量、维度和空文本验证
- 实现内部检索 DTO
- 定义 `VectorRepository` Protocol
- 实现 `QdrantRepository`
- Collection 使用 Cosine Distance
- 所有检索强制带 `workspace_id` Filter
- Qdrant Point ID 使用 PostgreSQL Chunk 主键
- 实现 `IndexingService`
- 在 PostgreSQL 提交成功后启动向量索引
- 实现 `DenseRetrievalService`
- 完成 Repository 与 Service 自动化测试
- 完成真实 Qdrant Smoke Test
- 为已有真实文档回填向量索引
- 建立 30 条人工 Golden Dataset
- 实现 Recall@5 与 MRR@5 评测脚本
- 失败案例写入本地 `retrieval_failures.jsonl`

### 关键设计

- Embedding 模型细节封装在 Provider 内，上层只依赖稳定接口。
- Repository 隐藏 Qdrant SDK，业务层只使用内部 DTO。
- PostgreSQL 是事实来源，Qdrant 是可重建检索索引。
- 文档与 Chunk 必须先提交 PostgreSQL，再写入 Qdrant。
- 所有 Workspace 共用环境级 Collection，但检索必须强制 Workspace 隔离。
- Qdrant Payload 保存检索与引用所需元数据，不保存完整 Chunk 正文。
- 评测使用人工标注的预期 Chunk，不用随机样例代替 Golden Dataset。

### 验收

- `python -m py_compile scripts/retrieval_eval.py`：PASS
- pytest：47 passed
- Qdrant Repository Smoke：PASS
- Qdrant Points：179
- Golden Dataset：30
- Recall@5：0.866667
- MRR@5：0.627778
- MISS：4

### 错误与修复

- 评测数据一度混用旧 Schema，导致 `KeyError`；统一为完整 `EvaluationCase` 字段。
- 一度使用未经逐条确认的批量 Golden，导致指标失真；重新按文档逐批人工标注。
- `TestClient` 与异步数据库跨事件循环，真实上传验证改为网页上传和独立异步检查。
- 评测脚本直接执行时缺少项目路径，统一从项目根目录使用 `PYTHONPATH=.`。
- 失败报告代码曾因片段粘贴位置错误产生缩进异常，最终以完整函数替换解决。

---

## Day 6：可信问答

### 完成

- 新增 Answer DTO
- 新增 Citation DTO
- 实现 Context Builder
- 实现 Context Enricher
- 实现 DeepSeek Provider
- 实现 AnswerService
- 将 Dense Retrieval 接入回答链路
- 根据检索结果回查 PostgreSQL Chunk 正文
- 实现 `POST /answers`
- 支持 Citation 返回
- 支持 Refused
- 完成真实 DeepSeek E2E

### 关键设计

- Retrieval 只负责召回，不负责回答。
- PostgreSQL 是 Chunk 正文事实来源，Qdrant 不保存完整正文。
- Dense Retrieval 返回 Chunk ID，再回查 PostgreSQL 获取上下文。
- Context Builder 负责组织 Prompt，不负责执行检索。
- AnswerService 统一协调 Workspace 校验、检索、上下文构建与 LLM 调用。
- Citation 来源于真实检索结果，而不是模型自由生成。
- 无证据时返回 Refused，而不是生成可能错误的回答。

### 验收

- `POST /answers`：PASS
- Workspace 校验：PASS
- Dense Retrieval：PASS
- PostgreSQL Chunk 回查：PASS
- Context Builder：PASS
- DeepSeek：PASS
- Citation：PASS
- Refused：PASS
- Real End-to-End：PASS

### 错误与修复

- 最初误判数据库 Session 导入位置，重新阅读项目代码后改为正确依赖。
- Answer E2E 初期仅验证业务逻辑，最终改为真实 PostgreSQL、Qdrant、Embedding 与 DeepSeek 全链路验证。
- 首次请求耗时较长，经日志确认主要来自 Sentence Transformer 冷启动，而不是数据库查询。
- 回答引用旧项目状态，最终确认属于知识库文档未更新，而不是 Retrieval 或 Answer Pipeline 的问题。

---

## Day 7：回答质量评测与文档删除

### 完成

- 为 Document 增加 `deleted_at`。
- 新增 Alembic Migration：`79831d7f5198_add_document_soft_delete.py`。
- 新增 `DocumentService.delete_document()`。
- 实现 `DELETE /documents/{document_id}`。
- PostgreSQL 软删除成功提交后，再 Best-effort 清理 Qdrant。
- Qdrant Repository 支持按 `workspace_id + document_id` 删除 Points。
- Dense Retrieval 排除已删除 Document。
- PostgreSQL Chunk 回查排除已删除 Document。
- 新增删除服务测试与删除 API 测试。
- 新增 `scripts/answer_eval.py`。
- 通过真实 `AnswerService` 执行回答评测并输出 JSONL。
- 完成删除后的三条无答案评测。
- 强化 LLM System Prompt 的主体一致性和证据充分性规则。

### 关键设计

- PostgreSQL 继续作为事实来源；Qdrant 只是可重建索引。
- 删除操作先提交 PostgreSQL，避免向量先删除而数据库事务失败。
- Qdrant Cleanup 失败不回滚 PostgreSQL 软删除。
- 当前使用 Best-effort Cleanup；长期方案为 Outbox Pattern。
- Retrieval 相关不代表 Evidence 足以支持结论。
- 回答前必须验证问题主体、证据主体、询问属性及其关系。
- Citation 只能来自实际进入 Context 的来源。

### 验收

- Soft Delete：PASS
- Deleted-document Retrieval Isolation：PASS
- Qdrant Cleanup：PASS
- Answer Evaluation：3/3 PASS
- Entity Scope Mismatch Fix：PASS
- pytest：119 passed
- Starlette/httpx 弃用警告：非阻塞

### 错误与修复

- 删除后的第一条问题仍返回 Pangu 模型名称。
- 检索返回的是未删除但不支持结论的 Pangu 文档，因此不是软删除故障。
- 根因是 Entity Scope Mismatch：证据只说明盘古平台提供该模型，不能证明 TechPilot 使用该模型。
- 修复 System Prompt 后，三条问题均拒答且 Citation 数量为 0。

---

## Day 8：引用、页码与上下文回归

### 完成

- 新增 `tests/answering/test_citation_traceability.py`。
- 增加 Markdown 标题路径跨层回归测试。
- 增加 PDF 页码范围跨层回归测试。
- 增加 Context Budget 排除来源的引用拒绝测试。
- 未修改生产代码，保持现有可信问答架构不变。

### 关键设计

- Citation Traceability 必须从 Parser 元数据一直追踪到服务端 Citation，不能只验证单层格式化函数。
- LLM 只允许返回内部 `SOURCE_N` 标识；文档名、页码、章节和原文均由服务端从实际进入 Context 的 Chunk 构造。
- 被 Context Budget 排除的 Chunk 不进入 `BuiltContext.sources`，因此对应来源标识必须被视为未知来源并拒绝。
- 回归测试优先证明既有设计边界，不因 Day 8 验收而引入不必要的生产逻辑修改。

### 验收

- Citation Traceability Tests：3 passed
- Markdown Heading Path Propagation：PASS
- PDF Page Range Propagation：PASS
- Omitted Source Citation Rejection：PASS
- Dependency Health：HTTP 200
- PostgreSQL / Redis / Qdrant：全部 `ok`
- pytest：122 passed
- `git diff --check`：PASS
- Starlette/httpx 弃用警告：非阻塞

### 错误与修复

- 首次全量测试中 `test_dependencies_health` 返回 503。
- 检查后确认不是 Day 8 代码回归，而是测试执行时依赖健康状态未满足。
- 启动并确认 PostgreSQL、Redis、Qdrant 后，依赖健康测试与全量测试均通过。

---

## Day 9：P1 集成验证与 Gate 证据

### 完成

- 新增 `tests/integration/test_p1_document_rag_lifecycle.py`。
- 通过 HTTP API 验证上传、回答、Citation、删除和删除后拒答。
- 生命周期测试使用真实 PostgreSQL、Embedding 和 Qdrant。
- 使用会校验 Prompt 的确定性 Fake LLM，要求 Prompt 中存在 `SOURCE_1` 和本次上传的唯一证据。
- 扩展 `scripts/answer_eval.py`，计算运行错误、过度拒答、正确拒答、错误回答和对应比率。
- 新增 `tests/scripts/test_answer_eval.py`，验证指标分母和 `n/a` 语义。
- 基于当前有效知识库重新构建 10 条困难无答案样本。
- 当前评测 Workspace 包含 5 份有效文档、1153 个 Chunk，其中盘古 PDF 为 1002 个 Chunk。
- 新增 `docs/P1_GATE_EVIDENCE.md`，集中整理 Day 10 Review 所需证据。

### 关键设计

- 自动化测试数量不等于 Gate 覆盖率；Gate 还要证明真实边界、生命周期、失败语义、指标和可复现命令。
- Fake LLM 负责确定性验证编排与数据边界；真实 DeepSeek E2E 负责验证 Provider、网络、请求格式和结构化返回。
- Fake LLM 不能无条件返回 `SOURCE_1`，否则会掩盖 Context 缺失或 Prompt 构造错误。
- 无答案评测中的运行错误单独统计，不能算作正确拒答或错误回答。
- `over_refusal_rate` 只对成功执行的 `answerable=true` 样本计算；全是无答案样本时必须输出 `n/a`。
- 回答正确不等于可信回答；答案完整性和 Citation 直接支持性必须同时成立。

### 验收

- P1 Lifecycle Integration：PASS
- Upload -> Persist -> Index -> Retrieve -> Answer -> Cite -> Delete -> Refuse：PASS
- Answer Evaluation Summary Tests：3 passed
- Full Test Suite：126 passed
- Dependency Health Test Repeated 3 Times：PASS
- `git diff --check`：PASS
- Unanswerable Cases：10
- Correct Refusals：10
- Incorrect Answers：0
- Incorrect-answer Rate：0.000000
- Runtime Errors：0
- Starlette/httpx 弃用警告：非阻塞

### 错误与修复

- 全量测试曾在生命周期测试之后使 `test_dependencies_health` 返回 503，但单独请求依赖健康接口为 200。
- 根因是全局 SQLAlchemy AsyncEngine 可能保留绑定到 pytest 事件循环的 asyncpg 连接；后续同步 TestClient 使用另一个事件循环。
- 在生命周期测试清理阶段执行 `await engine.dispose()`，强制后续测试建立新连接；未修改生产请求逻辑。
- 第一版 10 条无答案问题在未知实际知识库内容时生成，证据基础不可靠，已废弃。
- 重新核对当前有效 Document 与 Chunk，清理重复文档，并基于包含 1002 个 PDF Chunk 的完整语料重建最终评测集。

---

## Day 10：P1 Gate Review

### 结论

- P1 Gate：`CONDITIONAL PASS`
- P1 核心工程闭环已满足：Upload -> Index -> Retrieve -> Answer -> Cite -> Delete -> Refuse
- Dense Retrieval Baseline、Citation Traceability、无答案安全性和 126 条回归测试均有可复现证据

### 唯一条件

- 补充包含正常样本与易混淆样本的 `answerable=true` 定量评测集
- 记录每条样本的 answer correctness
- 记录 Citation 是否直接支持答案中的全部关键结论
- 汇总 over-refusal
- 保留失败样本并审查，不以 `refused=false` 或单次真实 E2E 代替质量结论

### 非条件范围

- OCR 不作为 P1 条件，属于后续文档摄取增强
- Hybrid Retrieval 不作为 P1 条件，属于 P2 检索优化
- Reranker 不作为 P1 条件，属于 P2 检索优化

### 文档同步

- README 更新为 P1 当前真实能力、运行方式、指标和 `CONDITIONAL PASS` 状态
- `PROJECT_STATUS.md` 更新 Day 10 结论与唯一条件
- `P1_GATE_EVIDENCE.md` 固化 Gate 决策、边界与条件关闭清单
- `INTERVIEW_NOTES.md`、`RUNBOOK.md` 同步判断口径和执行步骤
- PR #2 保持 Draft、未合并

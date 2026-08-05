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
- `INTERVIEW_NOTES.md`、`RUNBOOK.md` 同步判断口径和执行步骤
- PR #2 保持 Draft、未合并

## Day 11：P1 Answerable 质量复验与 FIX 判定

### 完成

- 建立 7 条 `answerable=true` 正常样本并明确 reference answer / acceptance criteria。
- 使用真实 `AnswerService`、DeepSeek 和生产 Dense Top-5 运行统一复验。
- 人工审查 answer correctness、Citation support、over-refusal 与运行错误。
- 在生产 `SYSTEM_PROMPT` 增加输出隔离：`SOURCE_N` 只能作为内部 Citation ID，不得进入用户可见正文。
- 将 BM25 / RRF / Reranker / hierarchical retrieval 预实验归档到 `.local/day11/diagnostics/`，不作为 P1 生产证据。
- P1 Gate 从 `CONDITIONAL PASS` 更新为 `FIX`。

### 关键设计

- Answer correctness 与 Citation support 必须分开判断；模型碰巧答对但证据不支持仍然失败。
- 生成 Prompt 只能约束模型如何使用已有证据，不能补回 Retriever 没召回的权威 Chunk。
- P1 失败样本首先定位 Retrieval rank，再判断生成层；不能看到错误答案就只调 Prompt。
- 诊断实验与生产实现必须隔离，避免用一次性脚本冒充正式 P2 能力。

### 验收

- Answerable samples：7
- Answer correctness：4/7
- Citation support：4/7
- Over-refusal：1/7
- Runtime errors：0
- `SOURCE_N` user-visible leak：0
- 失败目标 Dense rank：56 / 1120 / 26
- P1 Gate：FIX

### 错误与修复

- 最初 P1 `CONDITIONAL PASS` 只缺 answerable 质量证据；补证据后发现实际质量未达到 PASS，而不是“条件自动关闭”。
- 失败样本的权威 Chunk 未进入 Top-5，继续修改 Prompt 无法修复；后续转入 P2 检索优化。

## Day 12：BM25 实现、评测与 Dense/BM25 互补分析

### 完成

- 实现 `bm25_tokenizer.py`、BM25 DTO、PostgreSQL Chunk Repository 和 `BM25RetrievalService`。
- BM25 参数配置化：`k1=1.5`、`b=0.75`。
- Tokenizer 支持中英混合技术语料，完整保留 `workspace_id`、`Recall@5`、`multilingual-e5-base`、`SHA-256`、数字等 token。
- BM25 Repository 强制 Workspace 隔离、软删除过滤，并排除 PENDING/FAILED Document，只检索 COMPLETED/PARTIAL。
- 正式 BM25 scoring 只使用 `chunk.text`，与 Dense 的正文检索单元对齐。
- 新增 BM25 focused tests、数据库过滤集成测试和正式评测脚本。
- Full Test Suite：PASS。
- 对当前 30 条 Golden 做 integrity audit，发现并修复 6 条 stale label。
- 在 30/30 valid 的同一 Golden / corpus 上重跑 Dense 与 BM25。

### 关键设计

- Dense 依赖语义 embedding；BM25 依赖 lexical overlap。两者不是替代关系，融合价值要看失败集合是否互补。
- 统一 Chunk identity 的作用是实体对齐，不是“统一排名”：后续 fusion / dedupe /正文回查必须知道两路结果是否指向同一知识单元。
- Workspace / deleted / status filter 必须约束候选集合，不能先对全库 Top-K 再事后过滤，否则会产生截断偏差和隔离风险。
- Ingestion 可能在 Chunk 已提交后因为向量索引失败把 Document 标为 FAILED，因此 BM25 不能只看 `deleted_at`，还必须排除 FAILED。
- Day 12 不把 BM25 接入 `AnswerService`；生产回答仍使用 Dense，避免提前引入 Day 13 Hybrid 接口重构。
- Retrieval benchmark 必须绑定 corpus snapshot。stale Golden 是评测数据错误，不是 Retriever MISS。

### 验收

- Golden integrity：30/30 valid
- Dense：Recall@5 0.700000 / MRR@5 0.500000 / MISS 9
- BM25：Recall@5 0.700000 / MRR@5 0.567778 / MISS 9
- BM25-only hit：4
- Dense-only hit：4
- Both miss：5
- Hit union：25/30（理论覆盖上界 0.833333）
- Focused / integration / full regression：PASS

### 错误与修复

- 首版 BM25 eval 直接执行 `python scripts/...` 导致 `ModuleNotFoundError: app`；统一从项目根目录使用 `python -m scripts.bm25_retrieval_eval`。
- 新 BM25 集成测试后 P1 lifecycle / health 出现 asyncpg event-loop `RuntimeError` / `InterfaceError`；在测试清理阶段 `await engine.dispose()`，只重置测试连接池，不修改生产逻辑。
- 首版 tokenizer 先让 jieba 切分再保护技术 token，无法严格保证复杂标识符；改为先从原文识别 ASCII 技术 token，中文片段再交给 jieba。
- 原 BM25 Recall@5 0.60 的 30-case 结果包含 6 条 stale Golden；确认旧文档已删除且知识不再存在后，用当前新文档的人工权威 Chunk 替换并重跑。旧跨 snapshot 指标全部废弃。

---
## Day 13：RRF Hybrid Retrieval

### 完成

- 新增 `app/retrieval/rrf.py`，实现纯 Reciprocal Rank Fusion。
- 新增 `HybridSearchHit` 与 `HybridRetrievalService`。
- Dense 与 BM25 保持独立，通过统一 `chunk_id` 在 Fusion 层聚合。
- 明确 `candidate_limit` 与 final `limit` 的职责边界。
- Hybrid 结果保留两路原始 rank / score 和 `rrf_score` 供诊断。
- 增加 shared identity consistency check，避免同一 `chunk_id` 对应冲突实体。
- 新增 RRF 与 Hybrid Service 自动化测试。
- 新增 `scripts/hybrid_retrieval_eval.py`，同一次 Dense/BM25 candidate snapshot 同时计算 Dense/BM25/Hybrid 指标。
- 评测前增加 strict Golden integrity check。
- 完成真实 Hybrid Service smoke。
- 新增 `docs/adr/ADR-001-agent-runtime.md`，冻结 Thin Agent / Thick Harness 和 v1/v2 边界；未实现 Agent Runtime。

### 正式 Retrieval 结果

配置：

```text
candidate_limit=20
top_k=5
rrf_k=60
```

结果：

```text
Dense   Recall@5=0.700000  MRR@5=0.500000  MISS=9
BM25    Recall@5=0.700000  MRR@5=0.567778  MISS=9
Hybrid  Recall@5=0.766667  MRR@5=0.588333  MISS=7
```

失败集合：

```text
Dense-only hit=4
BM25-only hit=4
Both hit=17
Both miss=5
Dense/BM25 oracle union=25/30
Hybrid actual=23/30
Fusion losses=2
Both candidate miss=3
```

### 参数实验与结论

只做有边界假设验证，没有大规模搜索：

```text
c20/k60 → Recall 0.766667, MRR 0.588333
c10/k60 → Recall 0.766667, MRR 0.595556，但 both-candidate-miss 3→4
c20/k20 → 与 c20/k60 Top-5 结果相同
c20/k1  → Recall 0.766667, MRR 0.586111
```

结论：

- 缩小 candidate depth 没有提高 Recall，反而降低候选覆盖。
- `k=20` 与 `k=60` 在当前 Golden 上 Top-5 相同。
- 极小 `k=1` 只把 fusion loss 从 case 11 转移到 case 09，总 Recall 不变且 MRR 更低。
- 停止继续调参，避免对 30 条 Golden 过拟合。
- 正式 baseline 保留 `candidate_limit=20 / rrf_k=60 / top_k=5`。

### 关键失败分析

- case 09：Dense rank 3 + BM25 rank 14。`k=60` 能利用跨路共识进入 Hybrid rank 5；`k=1` 过度强化单路头部后反而丢失。
- case 11：Dense rank 2，BM25 Top-20 完全 miss。较大 `k` 下被多个双路中等排名候选压出；`k=1` 可恢复，但会产生其他损失。
- case 15：BM25-only rank 5。双路合并后存在更多高优先候选，单路边界命中在 final Top-5 中天然脆弱。
- case 25：Dense Top-20 miss、BM25 rank 17；属于候选召回/排序问题，不是单纯 RRF 可解决。

### Golden Integrity 修复

Day 13 发现一条 `retrieval_golden.jsonl` 的 `expected_document_id` metadata 错误。

Day 12 的 HIT/MISS 脚本只按 `expected_chunk_id` 计算，所以修正后 Dense/BM25 指标完全复现；但这暴露了 Day 12 的 integrity check 对完整身份关系校验不足。

Day 13 strict check 改为同时校验 workspace、legal Document、document id/name、chunk id/index 和 section。

修复后：

```text
30/30 valid
5 legal documents
1153 legal chunks
dataset_sha256=e65e8490ef8e23018673712b2e595c6779e842094bd49d5d433fc5641bcef7f5
corpus_snapshot_sha256=1d393523789b235bcfc1f821491bf86c5bcd29f47e04da7ebb85362b9ad81b0e
```

### 验收

- RRF tests：PASS
- Hybrid Service tests：PASS
- Hybrid real-service smoke：PASS
- Full Test Suite：154 passed
- `git diff --check`：PASS
- Starlette/httpx deprecation warning：非阻塞
- HF Hub unauthenticated warning：非阻塞

### 设计边界

- Day 13 没有把 Hybrid 接入 `AnswerService`；生产回答链路仍为 Dense-only。
- Day 13 不实现 Reranker。
- RRF 不能达到 Dense/BM25 oracle union 是预期风险，Fusion Top-K 本身可能产生 truncation loss。
- Agent Harness 补充要求只做设计冻结，不抢 P2 主线；Day 13 没有实现 Tool Registry、Repo Explorer 或 Coding Agent。


---

## Day 14：Cross Encoder Reranker 与延迟/收益实验

### 完成

- 新增 `RerankerProvider`、`CrossEncoderRerankerProvider`、DTO 与独立 `RerankingService`
- 使用成熟 OSS `sentence-transformers`；正式模型 `BAAI/bge-reranker-v2-m3`
- PostgreSQL 继续提供权威 Chunk 正文
- focused tests、真实 MPS smoke、30-case strict Golden 正式评测完成
- 正式配置：`20 -> 20 -> 5`，`rrf_k=60`，`max_length=512`
- Hybrid+Reranker：Recall@5 `0.866667`、MRR@5 `0.766667`、MISS 4
- rescue 3、regression 0、原 Hybrid Top-5 命中保留 23/23
- rerank inference mean `2323.19 ms`、P95 `2956.97 ms`
- reranked total mean `3009.45 ms`、P95 `3699.76 ms`
- bounded `rerank_depth=40` 质量不变、延迟显著增加，因此停止继续 depth 网格调参
- 修正 Hybrid 深度 contract：单路 `candidate_limit=N` 时 fusion/rerank union 上限为 `2N`
- ADR 冻结 `ToolContract` / `ToolResult` 最小字段；未实现 Tool Registry / Agent Runtime
- Full Test Suite：183 passed
- `git diff --check`：PASS

### 关键结论

- Reranker 解决“已召回候选的排序”，不是万能召回器。
- case 11 / 15 / 26：Hybrid rank 7 / 9 / 11 -> rerank rank 1 / 1 / 2。
- case 1 / 2 / 3 在 Dense/BM25 Top-20 都缺失，Cross Encoder 无法恢复。
- case 25 的 RRF full rank 为 29；depth=40 虽让模型看到它，仍无法进入 Top-5。
- PostgreSQL 回查 mean 约 3 ms，性能瓶颈在 Cross Encoder inference。
- 当前质量收益明确，但交互式生产路径增量延迟过高，暂不接入 `AnswerService`。

### 设计修正

旧假设 `rerank_depth <= candidate_limit` 错把“单路召回深度”和“融合候选深度”当成同一概念。最终约束修正为：

```text
final_top_k <= rerank_depth <= 2 * candidate_limit
```

并同步更新 Hybrid Service、Reranking Service、eval validation 和 tests。
---

## Day 15：Evidence Verifier / Evidence Sufficiency

### 完成

- 新增 Evidence Verifier provider-neutral Protocol。
- 新增 Pydantic Evidence input/output contract，可直接导出 JSON Schema。
- 新增 DeepSeek Evidence Verifier Provider 与独立 System Prompt。
- Prompt version 从 `evidence-verifier-v1` 收紧到 `evidence-verifier-v2`。
- 将生产 `AnswerService` 接入 Evidence Verifier gate；Retrieval 仍保持 Dense-only。
- `INSUFFICIENT / CONFLICTING` 在生成前直接拒答。
- `SUFFICIENT` 后只把 verified supporting sources 发送给 Answer LLM。
- Citation 只允许来自 verified supporting sources。
- 新增真实 Verifier smoke、正式 Evidence Eval 和相关回归测试。
- 6-case reviewed local Golden 正式评测达到 state 6/6、primary reason 6/6。
- Full Test Suite：227 passed。
- `git diff --check`：PASS。

### 关键设计

- Retrieval Relevance 与 Evidence Sufficiency 必须拆开；“相关”不能推出“能证明”。
- Evidence Verifier 的输入来自真实 `BuiltContext.sources`，不能检查随后不会进入生成上下文的正文。
- Evidence provenance 采用通用 `source_type / source_ref / title / locator / text`，不把未来 `verify_evidence` Tool Contract 固化成 Document-only Schema。
- Pydantic 负责结构化输入输出校验，provider-neutral invariant validation 再检查 source identity、state/reason 和 source role 一致性。
- 拒答由 evidence state 驱动，不使用模型自报 confidence 作为 Gate。
- 生成上下文必须缩小为 verified supporting sources，否则模型仍可能从“相关但未验证”的来源取事实并把 Citation 错挂到合法 Source。
- `insufficient` 使用单一 primary reason。reason taxonomy 采用最小决定性原因，而不是级联罗列所有下游缺失。
- Evidence Verifier 作为未来 Thick Harness 的 Verification boundary 预留 Schema，但 Day 15 不实现 Tool Registry / Agent Runtime。

### 验收

- Evidence Verifier focused tests：PASS
- DeepSeek Evidence Verifier smoke：PASS
- Formal Evidence Eval：6/6 state exact
- Formal Evidence Eval：6/6 primary reason exact
- Runtime errors：0
- Full Test Suite：227 passed
- `git diff --check`：PASS
- 非阻塞警告：Starlette TestClient / httpx deprecation warning

### 错误与修复

- 第一版 Day 15 overlay 漏迁移旧 `AnswerService(...)` / `_build_answer(...)` 测试调用点，导致 6 条 full-regression 失败；补齐测试依赖和新 verification 参数后完整回归恢复通过。
- 第一版 `EvidenceItem` 使用 Document-specific 字段，不利于未来 Code/Web Evidence 复用；改为 provider-neutral provenance。
- 第一版结构化返回使用手工 JSON 校验，不完全满足项目“结构化输出经过 Pydantic 校验”的规范；改为 Pydantic contract，并保留 provider-neutral 业务 invariant validation。
- 第一版 `SUFFICIENT` 后仍把全部 Context 交给生成模型，存在从未验证来源取事实的风险；改为只渲染 verified supporting sources。
- `evidence-verifier-v1` 在 subject mismatch / relation missing case 上会级联追加下游 reason，formal reason exact match 只有 4/6；没有修改 Golden，而是将 Prompt/contract 升级为 v2 单一 primary reason，最终 6/6。

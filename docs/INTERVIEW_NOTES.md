# TechPilot INTERVIEW_NOTES

## Day 1

### FastAPI 的 `/health` 有什么作用？

检查应用进程是否能够正常响应。它不能证明数据库、缓存或向量库可用。

---

## Day 2

### PostgreSQL、Redis、Qdrant 分别承担什么职责？

- PostgreSQL：结构化业务数据和元数据
- Redis：高速缓存及后续异步任务支持
- Qdrant：Embedding 向量、Payload 和语义检索索引

### ORM 和 Alembic 有什么区别？

ORM 描述应用希望使用的数据结构；Alembic 管理真实数据库 Schema 从旧版本迁移到新版本的过程。

---

## Day 3：文档摄取与事务设计

### 为什么 Parser 和 Chunker 要分开？

Parser 尽量还原源文件结构；Chunker 生成适合检索的知识单元。二者变化原因不同，应独立测试和替换。

### 为什么 Document 要先提交 PENDING？

解析和入库可能失败。先提交 PENDING，可以在失败后保留 FAILED Document 和错误信息。

### 为什么标题不单独生成 Chunk？

真实文档验证显示 heading-only Chunk 会造成大量极短检索单元。标题应作为上下文注入正文。

---

## Day 4–5：基础检索

### 为什么要抽象 EmbeddingProvider？

上层服务只需要“文档向量化”和“查询向量化”能力，不应绑定 Sentence Transformers 的具体 API。

### PostgreSQL 和 Qdrant 谁是事实来源？

PostgreSQL 是事实来源，保存 Document 和 Chunk 正文；Qdrant 是可重建的向量索引。

### 为什么索引要在 PostgreSQL Commit 后执行？

避免 Qdrant 已经存在向量，但 PostgreSQL 事务随后回滚，产生无法追溯的孤立索引。

### 为什么 Qdrant 搜索必须强制带 workspace_id？

所有 Workspace 共用一个环境级 Collection。强制 Filter 才能保证租户数据隔离。

### Recall@5 是什么？

30 条评测问题中，只要目标 Chunk 出现在前 5 个结果内就算召回成功。当前为 0.866667。

### MRR@5 是什么？

对每条问题取目标 Chunk 排名的倒数，再对全部问题求平均。

### 为什么 Golden Dataset 必须人工标注？

检索质量不能由模型自己给自己定义答案。每条 Query 必须由人确认最相关目标 Chunk。

---

## Day 6：可信问答

### 为什么回答不能直接使用 Qdrant Payload 中的内容？

Qdrant 是可重建检索索引，不是事实来源。完整 Chunk 正文应从 PostgreSQL 回查。

### 为什么 Citation 不能完全交给 LLM 生成？

模型可能生成不存在、错位或不能支持结论的引用。系统应根据实际进入 Context 的 Chunk 构造 Citation。

### Context Builder 和 Retriever 的职责有什么区别？

Retriever 负责找出相关 Chunk；Context Builder 负责把 Chunk 排序、格式化和截断。

### 为什么无证据时应该拒答？

可信问答只在证据充分时回答。拒答可以降低幻觉和错误归因风险。

---

## Day 7：回答质量评测与文档删除

### 为什么 Document 使用软删除，而不是直接物理删除？

软删除可以保留审计信息和业务历史，并允许检索层立即通过状态过滤停止使用该文档。物理清理可以作为后续独立流程处理。

### 为什么先提交 PostgreSQL 软删除，再清理 Qdrant？

PostgreSQL 是事实来源。先提交数据库状态，可以保证业务上删除已经生效。Qdrant 是可重建索引，其清理失败不应让数据库删除事务回滚。

### Best-effort Cleanup 有什么风险？

Qdrant 可能短期保留已删除文档的孤立向量。当前通过 PostgreSQL 状态过滤保证回答链路不使用它们；长期可使用 Outbox Pattern 实现可靠异步重试。

### 什么是 Entity Scope Mismatch？

检索结果与问题在关键词或语义上相关，但证据描述的是另一个主体。模型把该事实错误归因给问题中的目标主体。

### 为什么 Retrieval Relevance 不等于 Evidence Sufficiency？

Retriever 优化的是语义相关性，而回答要求证据明确支持结论。证据必须同时匹配目标主体、询问属性，以及二者之间的关系。

### Day 7 的真实失败案例是什么？

问题询问 TechPilot 使用的 Embedding 模型，检索返回盘古平台文档。该文档只说明盘古平台提供 `Pangu-EmbeddingRank-zh`，不能证明 TechPilot 使用它。

### 如何修复主体错配？

System Prompt 要求回答前检查：

1. 问题主体与证据主体是否一致。
2. 证据是否支持询问的属性。
3. 证据是否明确表达主体与属性之间的关系。
4. 任一条件缺失时拒答且不返回 Citation。

### 如何验证删除和拒答链路？

删除支持 TechPilot 项目事实的文档后，运行真实 `AnswerService` 评测。三条问题均返回 `refused=True`、`citations=0`，结果为 3/3 PASS。

---

## Day 8：引用可追溯性回归

### 如何证明 Citation 不是模型编造的？

测试必须覆盖完整数据链路，而不只是检查最终 JSON 字段：Parser 提取页码或标题路径，Chunker 保留结构，Context Builder 为实际进入 Prompt 的 Chunk 分配 `SOURCE_N`，AnswerService 再根据该映射构造 Citation。LLM 只返回内部来源标识，不能决定文档名、页码、章节或原文。

### 为什么要测试 Markdown 标题路径和 PDF 页码的跨层传播？

单元测试只能证明某一层能格式化字段，不能证明真实来源元数据在多层转换中没有丢失。跨层回归测试可以验证来源结构从解析阶段一直到用户看到的 Citation 都保持一致。

### 为什么被 Context Budget 排除的来源必须无法引用？

被排除的 Chunk 没有实际进入 LLM Prompt，不能成为回答证据。Context Builder 不会为其建立有效的来源映射；如果模型返回对应 `SOURCE_N`，AnswerService 必须将其视为未知来源并拒绝，而不是生成看似合法的 Citation。

### Day 8 为什么没有修改生产代码？

代码审计显示现有设计已经满足服务端引用映射、页码与章节传播、未知来源拒绝等边界。Day 8 的缺口是缺少跨层验收证据，因此最小且正确的改动是补回归测试，而不是为了体现开发量重写稳定逻辑。
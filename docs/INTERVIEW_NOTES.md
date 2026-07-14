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

上层服务只需要“文档向量化”和“查询向量化”能力，不应绑定 Sentence Transformers 的具体 API。这样模型实现可以替换，业务代码和测试保持稳定。

### 为什么 E5 的文档和查询要使用不同前缀？

E5 按检索任务训练，文档使用 `passage:`，查询使用 `query:`。Provider 统一处理前缀，避免调用方遗漏。

### 为什么 Repository 不直接返回 Qdrant SDK 对象？

业务层不应依赖具体向量库。Repository 把 Qdrant 对象转换成内部 DTO，使存储实现可替换，并缩小 SDK 变化的影响范围。

### PostgreSQL 和 Qdrant 谁是事实来源？

PostgreSQL 是事实来源，保存 Document 和 Chunk 正文；Qdrant 是可重建的向量索引。向量写入失败时，不应丢失已经成功摄取的原始数据。

### 为什么索引要在 PostgreSQL Commit 后执行？

避免 Qdrant 已经存在向量，但 PostgreSQL 事务随后回滚，产生无法追溯的孤立索引。

### 为什么 Qdrant 搜索必须强制带 workspace_id？

所有 Workspace 共用一个环境级 Collection。强制 Filter 才能保证租户数据隔离，不能依赖调用方自觉。

### Recall@5 是什么？

30 条评测问题中，只要目标 Chunk 出现在前 5 个结果内就算召回成功。当前 26 条成功，因此 Recall@5 为 26/30，即 0.866667。

### MRR@5 是什么？

对每条问题取目标 Chunk 排名的倒数，再对全部问题求平均。排名越靠前，MRR 越高；未进入前 5 的问题贡献 0。

### 为什么 Golden Dataset 必须人工标注？

检索质量不能由模型自己给自己定义答案。每条 Query 必须由人确认最相关的目标 Chunk，否则指标可能衡量的是错误标签，而不是 Retriever。

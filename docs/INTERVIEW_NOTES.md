# TechPilot INTERVIEW_NOTES

## Day 1

### FastAPI 的 `/health` 有什么作用？

检查应用进程是否能够正常响应。它不能证明数据库、缓存或向量库可用。

---

## Day 2

### Docker 和 Docker Compose 分别解决什么问题？

Docker 用镜像创建隔离、可复制的容器运行环境。Docker Compose 用一个配置文件统一描述并启动多个相关服务。

### PostgreSQL、Redis、Qdrant 分别承担什么职责？

- PostgreSQL：结构化业务数据和元数据
- Redis：高速缓存及后续异步任务支持
- Qdrant：Embedding 向量、Payload 和语义检索索引

### ORM 是什么？

ORM 将 Python 对象与关系型数据库表进行映射。类对应表，对象通常对应表中的一行。

### Alembic 是什么？

Alembic 管理数据库 Schema 的版本变化。ORM 描述目标结构，Alembic 生成并执行从旧结构迁移到新结构的步骤。

### `/health` 与 `/health/dependencies` 有什么区别？

- `/health`：Liveness，检查 FastAPI 进程是否存活
- `/health/dependencies`：Readiness，检查 PostgreSQL、Redis、Qdrant 是否可用

---

## Day 3：文档摄取与事务设计

### 为什么 Parser 和 Chunker 要分开？

Parser 的目标是尽量还原源文件结构；Chunker 的目标是生成适合检索的知识单元。两者变化原因不同，分开后可以独立测试和替换。

### 为什么需要 `_ChunkCandidate`？

Markdown 和 PDF 有不同的拆分规则，但 `chunk_index`、稳定 ID、字符统计和最终输出结构应该统一。Candidate 表示内容边界已经确定，但尚未分配最终身份。

### 为什么 Document 要先提交 PENDING？

解析和入库可能失败。如果只使用一个事务，失败时 Document 也会消失，无法审计失败文件。先提交 PENDING，可以在失败后更新为 FAILED 并保存错误信息。

### 为什么 Chunk Metadata 使用 JSONB？

页码、标题路径、元素类型和拆分序号并非每种文件都有。JSONB 可以保存可扩展结构，并支持 PostgreSQL 字段级查询。

### 为什么标题不单独生成 Chunk？

真实文档验证显示，heading-only Chunk 会造成大量极短检索单元。标题应作为结构和上下文注入正文，而不是独立检索结果。

### Route 和 Router 的区别是什么？

Route 是一个具体的 HTTP 方法与路径，例如 `POST /documents/upload`。Router 是用于组织一组相关 Route 的容器。

### 为什么 Service 不直接返回 HTTPException？

Service 属于业务层，不应依赖 HTTP 协议。它抛出业务异常，由 API 层转换成 404、415 或 422。

### 为什么失败时要 rollback？

第二阶段的 Chunk 与最终状态必须原子提交。任何一步失败，都应撤销未提交 Chunk，避免出现部分入库。

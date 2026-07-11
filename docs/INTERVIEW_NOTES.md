# TechPilot INTERVIEW_NOTES

## Day 1

### FastAPI 的 `/health` 有什么作用？
检查应用进程是否能够正常响应。它不能证明数据库、缓存或向量库可用。

---

## Day 2

### Docker 和 Docker Compose 分别解决什么问题？
Docker 用镜像创建隔离、可复制的容器运行环境。Docker Compose 用一个配置文件统一描述并启动多个相关服务。

### 为什么固定镜像版本，而不使用 `latest`？
避免不同时间拉取到不同版本，导致开发环境和团队环境不一致。

### PostgreSQL、Redis、Qdrant 分别承担什么职责？
- PostgreSQL：结构化业务数据和元数据
- Redis：高速缓存及后续异步任务支持
- Qdrant：Embedding 向量、Payload 和语义检索索引

### ORM 是什么？
ORM 将 Python 对象与关系型数据库表进行映射。类对应表，对象通常对应表中的一行。

### SQLAlchemy 和 asyncpg 的关系是什么？
SQLAlchemy 提供 ORM 和数据库抽象；asyncpg 是实际与 PostgreSQL 异步通信的驱动。

### Alembic 是什么？
Alembic 管理数据库 Schema 的版本变化。ORM 描述目标结构，Alembic 生成并执行从旧结构迁移到新结构的步骤。

### 为什么 Chunk 必须保存 `document_id`？
这是 Chunk 与 Document 的直接归属关系。删除后数据库无法推导 Chunk 属于哪份文档，因此它不是冗余字段。

### 为什么 Chunk 不直接保存 `workspace_id`？
Document 已经通过 `workspace_id` 关联 Workspace，可以沿 `Chunk → Document → Workspace` 查询。重复保存会增加不一致风险。

### 为什么保存 `page`？
系统最终必须给用户可核验的来源。用户原始文档中没有 Chunk 编号，但有页码，因此 `page` 支持引用可追溯和引用正确率评测。

### 为什么 Document 使用 `status` 而不是 `is_finished`？
处理流程不只有完成和未完成，还可能处于等待、处理中、成功或失败。状态字段比布尔值表达力更强。

### `/health` 与 `/health/dependencies` 有什么区别？
- `/health`：Liveness，检查 FastAPI 进程是否存活
- `/health/dependencies`：Readiness，检查 PostgreSQL、Redis、Qdrant 是否可用

### 为什么数据库健康检查使用 `SELECT 1`？
目标仅是验证连接、SQL 执行和响应能力，不需要读取业务表。`SELECT 1` 成本低、依赖少。

### 为什么三个依赖应并发检查？
三个依赖彼此独立。并发检查既能返回完整诊断信息，也能把总耗时接近最慢单项，而不是三项耗时之和。

### 为什么返回 `latency_ms`？
状态为 `ok` 只说明能够响应；延迟能暴露依赖正在变慢的问题，是健康程度和性能退化的早期信号。

### 为什么依赖失败返回 503？
FastAPI 进程可能仍存活，但核心依赖不可用，系统不具备正常提供服务的条件。Body 可以返回 `degraded`，HTTP 使用 `503 Service Unavailable`。

### Day 2 的完整数据链路
```text
.env
  → Pydantic Settings
  → SQLAlchemy AsyncEngine
  → asyncpg
  → PostgreSQL

ORM Models
  → Base.metadata
  → Alembic Migration
  → PostgreSQL Tables

FastAPI Route
  → Health Service
  → PostgreSQL / Redis / Qdrant
```

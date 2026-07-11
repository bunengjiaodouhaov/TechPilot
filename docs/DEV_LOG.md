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

### 纠正
- 原记录将 Day 2 错写为“文档上传与 RAG”。
- 按总控手册，Day 2 应为基础设施：PostgreSQL、Redis、Qdrant、数据库连接、Alembic、最小数据表和依赖健康检查。

---

## Day 2：基础设施

### 完成
- 安装并验证 Docker Desktop 与 Docker Compose
- 新增 `compose.yaml`
- 启动并验证：
  - PostgreSQL
  - Redis
  - Qdrant
- 新增 `.env.example`
- 新增统一配置模块 `app/core/config.py`
- 新增 SQLAlchemy 异步连接模块
- 初始化 Alembic
- 设计并实现：
  - Workspace
  - Document
  - Chunk
- 生成并执行首条迁移
- 实现 `GET /health/dependencies`
- 增加依赖健康检查自动化测试
- pytest：2 passed

### 验证结果
- PostgreSQL：accepting connections
- Redis：PONG
- Qdrant：healthz check passed
- Alembic：数据库位于最新 head
- PostgreSQL 中存在 4 张表：
  - `alembic_version`
  - `workspace`
  - `document`
  - `chunk`
- `/health/dependencies`：HTTP 200，三项依赖均为 `ok`

### 当日关键理解
- Docker Compose 描述并统一启动项目依赖。
- ORM 将 Python 对象映射为关系型数据库表。
- Alembic 管理数据库 Schema 的版本变化。
- 外键保存关系，不属于可删除的冗余数据。
- `page` 服务于引用可追溯，而不是数据库本身。
- `/health` 检查进程存活；`/health/dependencies` 检查服务是否具备完整运行条件。
- 健康检查应并发检查独立依赖，并返回状态与延迟。
- 任一必要依赖失败时，整体状态为 `degraded`，HTTP 返回 503。

### 已知问题
- pytest 出现 Starlette/httpx 弃用警告，不阻塞当前阶段。
- 本机 Anaconda `base` 与项目 `.venv` 容易混淆；开发前必须确认解释器路径。

### 下一步
Day 3：文档上传、解析、稳定 Chunk 标识与元数据入库。

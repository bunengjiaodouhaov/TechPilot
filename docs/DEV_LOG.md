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
- 启动并验证 PostgreSQL、Redis、Qdrant
- 新增 `.env.example`
- 新增统一配置模块 `app/core/config.py`
- 新增 SQLAlchemy 异步连接模块
- 初始化 Alembic
- 设计并实现 Workspace、Document、Chunk
- 生成并执行首条迁移
- 实现 `GET /health/dependencies`
- 增加依赖健康检查自动化测试
- pytest：2 passed

### 当日关键理解

- Docker Compose 描述并统一启动项目依赖。
- ORM 将 Python 对象映射为关系型数据库表。
- Alembic 管理数据库 Schema 的版本变化。
- `/health` 检查进程存活；`/health/dependencies` 检查服务是否具备完整运行条件。

---

## Day 3：第一条数据链路

### 完成

- 新增 Markdown 和 PDF Parser
- 建立统一 `ParseInput`、`ParsedElement`、`ParsedDocument`
- 实现 ParserRouter
- 实现 StructureAwareChunker
- 新增稳定 `chunk_id`
- 新增 Chunk JSONB Metadata
- 扩展 Document 摄取字段
- 新增 DocumentStatus
- 实现 IngestionService
- 实现 FastAPI 文件上传接口
- 新增数据库 Session dependency
- 新增真实 E2E 验证脚本
- 自动化测试达到 17 passed

### 关键设计

- Parser 负责结构还原，Chunker 负责检索单元。
- 文件类型相关规则和公共 finalize 逻辑分离。
- `chunk_index` 表示顺序，不参与稳定身份生成。
- 标题路径保存在 `section` 和 Metadata 中。
- 标题注入正文 Chunk，但不单独成为检索 Chunk。
- Document 在解析前先以 `PENDING` 提交。
- Chunk 和最终 Document 状态原子提交。
- 失败时回滚未提交 Chunk，并保留 FAILED Document。
- Python Enum 约束代码写法，PostgreSQL CHECK 约束数据库值。
- JSONB 用于保存不同文件类型的可扩展 Metadata。

### 真实文档反馈

第一版切块策略把标题单独生成 Chunk：

- 总 Chunk：312
- heading-only：133
- 小于 50 字符：113

调整为“标题只作为上下文”后：

- 总 Chunk：179
- heading-only：0
- 小于 50 字符：0

这说明切块策略不能只通过小型单元测试判断，必须使用真实文档分析分布。

### 当日关键理解

- Route 是一个具体 HTTP 地址，Router 是一组 Route 的容器。
- ORM 修改不会自动改变真实数据库，必须通过 Alembic migration。
- `default` 是 SQLAlchemy 默认值，`server_default` 是数据库默认值。
- Service 负责业务事务，API 层负责 HTTP 输入输出和异常映射。
- 单元测试、API 测试和真实 E2E 分别验证不同边界。
- 测试失败不一定表示业务代码错误，也可能是测试期望没有同步设计变化。

### 验收

- 17 个自动化测试通过
- Alembic 位于最新 head
- Markdown/PDF 真实上传通过
- 5 份真实文档通过 Swagger 入库
- Document 状态全部为 COMPLETED
- 所有 Document 都产生有效 Chunk

# TechPilot PROJECT_STATUS

## 当前版本
v0.2-dev

## 当前阶段
P0 工程骨架

## 阶段状态
- Day 1：已完成
- Day 2：已完成
- Day 3：待开始

## 已完成

### Day 1：冻结项目
- 创建 GitHub 仓库并完成首次提交
- 提交 `docs/product-baseline.md`
- 创建 P0–P7 GitHub Milestones
- 创建 FastAPI 最小工程
- 实现 `GET /health`
- Swagger 可访问
- 基础自动化测试通过

### Day 2：基础设施
- 使用 Docker Compose 启动 PostgreSQL、Redis、Qdrant
- 创建 `.env.example`，本地 `.env` 已被 Git 忽略
- 使用 Pydantic Settings 统一读取环境配置
- 建立 SQLAlchemy 异步数据库连接
- 初始化 Alembic
- 建立 `Workspace`、`Document`、`Chunk` 三个最小 ORM 模型
- 生成并执行首条数据库迁移
- 实现 `GET /health/dependencies`
- PostgreSQL、Redis、Qdrant 依赖检查均返回状态与延迟
- 自动化测试：2 passed

## 当前可验证证据
- `docker compose ps`
- `alembic current`
- PostgreSQL 中存在 `workspace`、`document`、`chunk`、`alembic_version`
- `GET /health/dependencies` 返回 HTTP 200 和三项依赖状态
- `pytest -q` 返回 2 passed

## 已知非阻塞问题
- FastAPI TestClient 触发 Starlette/httpx 弃用警告；当前不影响测试结果，后续在依赖升级任务中统一处理。

## 下一步
Day 3：第一条数据链路
- 准备 5 份官方技术文档或论文
- 实现文档上传
- 实现 PDF / Markdown 解析
- 保留 PDF 页码与 Markdown 标题路径
- 生成稳定 `chunk_id`
- 保存 Document 与 Chunk Metadata
- 完成上传 → 解析 → 入库的端到端测试

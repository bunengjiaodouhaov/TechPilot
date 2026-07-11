# TechPilot Day 2 Review

> 本文件只保留本地，不提交 GitHub。

## 今日阶段
P0 工程骨架 — Day 2 基础设施

## 今日唯一主目标
让 FastAPI 能够连接并检查 PostgreSQL、Redis、Qdrant，并建立第一版最小数据模型。

## 实际完成
- Docker Desktop 与 Compose 环境可用
- PostgreSQL、Redis、Qdrant 容器启动并验证
- `.env.example` 与统一配置模块
- SQLAlchemy 异步数据库连接
- Alembic 初始化、生成并执行首条迁移
- Workspace、Document、Chunk 三个最小模型
- `/health/dependencies`
- 自动化测试：2 passed
- 补齐 Day 1 的 P0–P7 GitHub Milestones

## 当前系统链路

```text
FastAPI
├── PostgreSQL：Workspace / Document / Chunk
├── Redis：缓存与后续任务基础
└── Qdrant：向量与语义检索基础
```

```text
ORM Models
→ Alembic Migration
→ PostgreSQL Tables
```

## 必须理解
- Docker Image 与 Container 的区别
- Compose 的 service、image、ports、environment、volume
- 环境变量为什么不能硬编码
- ORM、SQLAlchemy、asyncpg、Alembic 的职责区别
- Workspace → Document → Chunk 的关系
- 外键与冗余数据的区别
- page 对可信引用的意义
- Liveness 与 Readiness 的区别
- 503 Service Unavailable 的意义
- latency 是实际响应耗时，不是“还需等待多久”

## 当前不要求背诵
- `mapped_column` 的完整参数
- SQLAlchemy 异步 API 的准确拼写
- Alembic `env.py` 模板
- `asyncio.gather` 的完整语法
- Redis、HTTPX 客户端的具体初始化语法

要求是能读懂其职责，并在文档或 AI 协助下正确修改。

## 今日错误与修复

### 1. Docker 命令不存在
原因：未安装 Docker Desktop。  
修复：安装 Apple Silicon 版本并验证 Docker / Compose。

### 2. `.venv` 激活后没有 Python
原因：旧虚拟环境链接到 Anaconda Python，环境失效。  
修复：安装 Python 3.11，重建 `.venv`。

### 3. Alembic 缺少 greenlet
原因：SQLAlchemy 异步迁移路径缺少运行依赖。  
修复：安装 `greenlet` 并重新执行 `alembic current`。

### 4. pytest 报找不到 FastAPI
原因：终端处于 Anaconda `(base)` 而不是项目 `.venv`。  
修复：退出 Conda、激活 `.venv` 后测试通过。

### 5. Day 1 Milestones 遗漏
原因：Day 1 只检查了代码，没有完整对照手册验收。  
修复：创建 P0–P7 GitHub Milestones，并把“手册验收”加入每日收尾规则。

## 面试自测
1. 为什么需要 Docker Compose？
2. PostgreSQL、Redis、Qdrant 各自存什么？
3. ORM 和 Alembic 有什么区别？
4. 为什么 Chunk 需要 document_id，但通常不需要 workspace_id？
5. 为什么 RAG Chunk 必须保存 page？
6. 为什么健康检查使用 SELECT 1？
7. 为什么三个依赖要并发检查？
8. 为什么 degraded 状态返回 HTTP 503？
9. 如果 Qdrant 写入到一半失败，如何避免用户重新上传？
10. 目前模型设计中哪些字段属于事实、关系、计算结果？

## 明日第一任务
按 Day 3 手册准备 5 份官方技术文档或论文，并先设计文档上传、解析、页码保留和元数据入库链路。

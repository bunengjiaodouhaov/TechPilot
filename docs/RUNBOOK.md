# TechPilot RUNBOOK

## 1. 每天开始开发

```bash
cd ~/TechPilot
conda deactivate 2>/dev/null || true
source .venv/bin/activate
which python
python --version
git pull
```

预期：
- `which python` 指向 `TechPilot/.venv/bin/python`
- Python 版本为 3.11.x

## 2. 启动基础设施

确保 Docker Desktop 已启动，然后执行：

```bash
docker compose up -d
docker compose ps
```

验证依赖：

```bash
docker compose exec postgres pg_isready -U techpilot -d techpilot
docker compose exec redis redis-cli ping
curl http://localhost:6333/healthz
```

预期：
- PostgreSQL：`accepting connections`
- Redis：`PONG`
- Qdrant：`healthz check passed`

## 3. 数据库迁移

查看当前版本：

```bash
alembic current
```

应用最新迁移：

```bash
alembic upgrade head
```

查看数据库表：

```bash
docker compose exec postgres   psql -U techpilot -d techpilot   -c "\dt"
```

新增或修改 ORM 模型后：

```bash
alembic revision --autogenerate -m "<migration message>"
```

必须先审查生成的 migration，再执行：

```bash
alembic upgrade head
```

## 4. 启动 API

```bash
uvicorn app.main:app --reload
```

验证：

```bash
curl http://127.0.0.1:8000/health
curl -i http://127.0.0.1:8000/health/dependencies
```

API 文档：

```text
http://127.0.0.1:8000/docs
```

## 5. 运行测试

```bash
pytest -q
```

Day 2 基线：

```text
2 passed
```

当前存在一个非阻塞的 Starlette/httpx 弃用警告。

## 6. 每天结束开发

```bash
pytest -q
git status
git diff
git add -A
git commit -m "<type>: <message>"
git push
```

提交前必须：
- 对照总控手册检查当日验收项
- 更新 `PROJECT_STATUS.md`
- 更新 `DEV_LOG.md`
- 更新必要的运行命令
- 生成仅保留本地的当日 Review

## 7. 停止基础设施

停止容器但保留数据卷：

```bash
docker compose down
```

停止并删除数据卷（会清空本地数据库与向量数据，谨慎执行）：

```bash
docker compose down -v
```

## 8. 常见问题

### 终端显示 `(base)`，pytest 找不到 FastAPI

原因：正在使用 Anaconda 环境，而不是项目虚拟环境。

处理：

```bash
conda deactivate
source .venv/bin/activate
which python
```

### `python` 或 `pip` 找不到

检查 `.venv` 是否有效：

```bash
ls -l .venv/bin/python*
```

必要时使用 Python 3.11 重建：

```bash
rm -rf .venv
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

### Docker 命令找不到

安装并启动 Docker Desktop，再重新打开 VS Code Terminal。

### 依赖健康检查失败

依次执行：

```bash
docker compose ps
docker compose logs postgres
docker compose logs redis
docker compose logs qdrant
```

# TechPilot RUNBOOK

## 每天开始开发

```bash
conda deactivate 2>/dev/null || true
source .venv/bin/activate
which python
python --version
git pull
docker compose up -d
alembic upgrade head
uvicorn app.main:app --reload
```

验证：

- http://127.0.0.1:8000/health
- http://127.0.0.1:8000/health/dependencies
- http://127.0.0.1:8000/docs

## 每天结束开发

```bash
pytest -q
alembic current
git diff --check
git status
```

确认无误后：

```bash
git add -A
git commit -m "<message>"
git push
```

## Day 3：文档摄取

### 创建默认 Workspace

```bash
docker compose exec postgres psql           -U techpilot           -d techpilot           -c "INSERT INTO workspace (name) VALUES ('TechPilot Default') RETURNING id, name;"
```

### 网页上传

打开：

```text
http://127.0.0.1:8000/docs
```

执行：

```text
POST /documents/upload
```

填写：

```text
workspace_id=<实际 Workspace ID>
file=<Markdown 或 PDF>
```

### 常见 404

```json
{"detail": "Not Found"}
```

表示路由没有加载，通常需要重新启动 Uvicorn。

```json
{"detail": "Workspace 1 does not exist."}
```

表示路由已经执行，但 Workspace 不存在。

### 自动化测试

```bash
pytest -q
```

### Alembic 检查

```bash
alembic current
```

### 真实 E2E

```bash
python scripts/verify_upload_e2e.py
```

成功标志：

```text
E2E RESULT: PASS
```

### 查看文档和 Chunk 数量

```bash
docker compose exec -e PAGER=cat postgres psql           -U techpilot           -d techpilot           -P pager=off           -c "
SELECT
    d.id,
    d.name,
    d.status,
    COUNT(c.id) AS chunk_count
FROM document d
LEFT JOIN chunk c ON c.document_id = d.id
GROUP BY d.id, d.name, d.status
ORDER BY d.id;
"
```

### 切块质量检查

```bash
docker compose exec -e PAGER=cat postgres psql           -U techpilot           -d techpilot           -P pager=off           -c "
SELECT
    d.name,
    COUNT(c.id) AS total_chunks,
    COUNT(*) FILTER (
        WHERE c.metadata ->> 'heading_only' = 'true'
    ) AS heading_only_chunks,
    COUNT(*) FILTER (
        WHERE c.char_count < 50
    ) AS chunks_under_50_chars,
    ROUND(AVG(c.char_count), 1) AS avg_chars,
    PERCENTILE_CONT(0.5) WITHIN GROUP (
        ORDER BY c.char_count
    )::INTEGER AS median_chars,
    MAX(c.char_count) AS max_chars
FROM document d
JOIN chunk c ON c.document_id = d.id
GROUP BY d.id, d.name
ORDER BY d.id;
"
```

## 常见问题

### `No module named fastapi`

```bash
python -m pip install -r requirements.txt
```

### `requirements.txt` 找不到

确认当前目录是项目根目录。

### `ModuleNotFoundError: No module named 'app'`

从项目根目录运行模块，或确保脚本已把项目根目录加入 `sys.path`。

### 终端出现 `heredoc>`

说明 Shell 正在等待多行输入结束标记。按 `Ctrl+C` 取消，不要继续粘贴 Markdown 文档内容。

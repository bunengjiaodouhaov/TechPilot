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

### 真实 E2E

```bash
python scripts/verify_upload_e2e.py
```

成功标志：

```text
E2E RESULT: PASS
```

## Day 4–5：基础检索

### 启动依赖

```bash
docker compose up -d
```

### 编译检查

```bash
python -m py_compile app/retrieval/embedding.py app/retrieval/dto.py app/retrieval/repository.py app/retrieval/qdrant_repository.py app/retrieval/indexing_service.py app/retrieval/dense_retrieval_service.py scripts/retrieval_eval.py
```

### 自动化测试

```bash
pytest -q
```

### 运行 Dense Retrieval Baseline

从项目根目录执行：

```bash
PYTHONPATH=. python scripts/retrieval_eval.py
```

预期输出包含：

```text
DENSE RETRIEVAL BASELINE
evaluation_cases: 30
top_k: 5
recall_at_5: 0.866667
mrr_at_5: 0.627778
failure_report: eval/retrieval_failures.jsonl
```

### 本地评测文件

以下目录只保留本地，不提交 GitHub：

```text
eval/
```

其中可包含：

```text
retrieval_golden.jsonl
retrieval_failures.jsonl
source_chunks.local.jsonl
```

### 常见错误

#### `ModuleNotFoundError: No module named 'app'`

```bash
PYTHONPATH=. python scripts/retrieval_eval.py
```

#### `KeyError: expected_document_id`

说明 Golden Dataset 存在旧 Schema。每行必须包含：

```text
query
workspace_id
expected_document_id
expected_document_name
expected_chunk_id
expected_chunk_index
expected_section
```

#### `IndentationError`

不要在函数外拼接带缩进的片段。使用完整函数或完整文件替换，并先运行：

```bash
python -m py_compile scripts/retrieval_eval.py
```

#### Hugging Face 未认证警告

不影响本地模型加载和评测结果。需要提高下载限额时再配置 `HF_TOKEN`。

## Day 6：可信问答

### 启动服务

```bash
docker compose up -d
alembic upgrade head
uvicorn app.main:app --reload
```

### Swagger 验证

打开：

```text
http://127.0.0.1:8000/docs
```

执行：

```text
POST /answers
```

请求示例：

```json
{
  "workspace_id": "<实际 Workspace ID>",
  "question": "TechPilot 当前项目主要完成了哪些能力？"
}
```

预期结果：

```text
HTTP 200
refused = false
answer 非空
citations 非空
```

### 真实 E2E

```text
POST /answers
  ↓
Workspace 校验
  ↓
Dense Retrieval
  ↓
PostgreSQL Chunk 回查
  ↓
Context Builder
  ↓
DeepSeek
  ↓
Answer + Citation
```

检查项：

- PostgreSQL 中存在 Chunk
- Qdrant 中存在对应向量
- DeepSeek API Key 已配置
- 返回 Answer
- 返回 Citation
- 无异常报错

### 首次请求较慢

第一次请求会加载 Sentence Transformer 模型。日志可能出现：

```text
Loading weights: 100%
```

这属于正常冷启动。同一应用进程中的后续请求会复用已加载模型。

### 回答内容与当前代码状态不一致

优先检查引用的源文档是否已经过时。RAG 会基于知识库内容回答；若知识库仍描述早期项目阶段，模型可能正确引用旧文档，但结论不符合当前代码状态。这属于知识库维护问题，不应直接判断为检索代码故障。

## 通用常见问题

### `No module named fastapi`

```bash
python -m pip install -r requirements.txt
```

### `requirements.txt` 找不到

确认当前目录是项目根目录。

### 终端出现 `heredoc>`

Shell 正在等待多行输入结束标记。按 `Ctrl+C` 取消。

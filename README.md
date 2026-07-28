# TechPilot

TechPilot 是一个面向开发者的技术文档检索与可信问答项目。当前版本完成了 P1 文档 RAG 主链路：文档摄取、Dense Retrieval、基于证据的回答、服务端 Citation、拒答和删除隔离。

## P1 Gate 状态

**CONDITIONAL PASS**

P1 核心工程链路与无答案安全性已经通过验证。转为最终 `PASS` 的唯一条件是：

- 补充包含正常样本与易混淆样本的 `answerable=true` 定量评测集；
- 记录 answer correctness、citation support 和 over-refusal；
- 保留并审查失败样本，不用单次 E2E 或“未拒答”代替回答质量结论。

OCR、Hybrid Retrieval 和 Reranker 不属于 P1 Gate 条件，分别作为文档摄取增强和 P2 检索优化范围处理。

## 当前能力

- Markdown 与文本型 PDF 上传、解析和结构优先 Chunking
- Markdown 标题路径、PDF 页码范围与稳定 `chunk_id`
- PostgreSQL 保存 Document、Chunk 正文和来源元数据
- `intfloat/multilingual-e5-base` 生成 768 维归一化 Embedding
- Qdrant Dense Top-K Retrieval 与 Workspace 隔离
- PostgreSQL 权威正文回查与 Context Builder
- DeepSeek 结构化回答与证据不足拒答
- 服务端根据实际进入 Context 的 `SOURCE_N` 构造 Citation
- Document 软删除、Qdrant Best-effort Cleanup 与删除后检索隔离
- 生命周期集成测试：Upload → Index → Retrieve → Answer → Cite → Delete → Refuse

## 可信回答边界

```text
文档
→ Parser / Chunker
→ PostgreSQL + Qdrant
→ Dense Retrieval
→ PostgreSQL 正文回查
→ Context Builder
→ DeepSeek 返回 SOURCE_N
→ 服务端绑定文档名、页码/章节和原文引用
```

Retriever 找到的是候选证据；只有实际进入 Context 的 Chunk 才能被引用。LLM 不直接生成文档名、页码、章节和原文引用，未知或被 Context Budget 排除的 `SOURCE_N` 会被拒绝。

## 技术栈

- Python 3.11+
- FastAPI
- PostgreSQL + SQLAlchemy Async + Alembic
- Qdrant
- Redis
- Sentence Transformers / multilingual E5
- DeepSeek
- pytest

## 本地运行

### 1. 配置环境

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

在 `.env` 中配置 `DEEPSEEK_API_KEY`。默认基础设施地址与 `docker-compose.yml` 一致。

### 2. 启动依赖与应用

```bash
docker compose up -d
alembic upgrade head
uvicorn app.main:app --reload
```

访问：

- 健康检查：<http://127.0.0.1:8000/health>
- 依赖检查：<http://127.0.0.1:8000/health/dependencies>
- Swagger：<http://127.0.0.1:8000/docs>

### 3. 创建 Workspace

当前没有单独的 Workspace 创建 API，可通过 PostgreSQL 创建本地 Workspace：

```bash
docker compose exec postgres psql \
  -U techpilot \
  -d techpilot \
  -c "INSERT INTO workspace (name) VALUES ('TechPilot Default') RETURNING id, name;"
```

## API

### 上传文档

```bash
curl -X POST http://127.0.0.1:8000/documents/upload \
  -F "workspace_id=<workspace_id>" \
  -F "file=@<path-to-document>"
```

支持 `.md`、`.markdown` 和带文本层的 `.pdf`。

### 提问

```bash
curl -X POST http://127.0.0.1:8000/answers \
  -H "Content-Type: application/json" \
  -d '{
    "workspace_id": 1,
    "question": "文档中的核心结论是什么？"
  }'
```

响应包含：

- `answer`
- `refused`
- `citations[].document_name`
- `citations[].page_start` / `page_end`
- `citations[].section`
- `citations[].quote`

### 删除文档

```bash
curl -X DELETE \
  "http://127.0.0.1:8000/documents/<document_id>?workspace_id=<workspace_id>"
```

删除成功返回 `204`。PostgreSQL 先完成软删除，Qdrant 随后执行 Best-effort Cleanup；回答与检索链路以 PostgreSQL 状态为准。

## 测试与评测

### 自动化与集成测试

```bash
pytest -q
git diff --check
```

P1 Gate 已验证：

```text
126 passed
P1 lifecycle integration: PASS
dependency health repeated 3 times: PASS
git diff --check: PASS
```

### Dense Retrieval Baseline

```bash
PYTHONPATH=. python scripts/retrieval_eval.py
```

已记录的 30 条人工标注 Baseline：

```text
Recall@5: 0.866667
MRR@5:    0.627778
MISS:     4
```

### Trusted Answering Evaluation

```bash
PYTHONPATH=. python scripts/answer_eval.py \
  --dataset eval/answer_golden.jsonl \
  --output eval/answer_results.jsonl \
  --retrieval-limit 5
```

已完成的无答案安全性结果：

```text
unanswerable cases:    10
correct refusals:      10
incorrect answers:     0
incorrect-answer rate: 0.000000
runtime errors:        0
```

这 10 条全部为 `answerable=false`，因此不能推导有答案质量，`over_refusal_rate` 仍为 `n/a`。P1 `CONDITIONAL PASS` 的唯一待补证据是包含正常与易混淆 `answerable=true` 样本的定量评测，并记录 answer correctness、citation support 和 over-refusal。

## 已知限制与阶段边界

- 仅支持 Markdown 与文本型 PDF；扫描型 PDF OCR 不在 P1 条件内。
- 当前检索为 Dense Top-K；Hybrid Retrieval 和 Reranker 属于 P2，不在 P1 条件内。
- Context Budget 使用字符数，不是真实 tokenizer token 数。
- 上传文件当前整体读入内存，尚未实现大小限制和流式处理。
- Qdrant 删除采用 Best-effort Cleanup，长期可演进为 Outbox Pattern。
- FastAPI TestClient 当前会产生 Starlette/httpx 弃用警告。

## 项目文档

- `docs/P1_GATE_EVIDENCE.md`
- `docs/PROJECT_STATUS.md`
- `docs/ARCHITECTURE.md`
- `docs/DEV_LOG.md`
- `docs/RUNBOOK.md`
- `docs/LEARNING_PROTOCOL.md`
- `docs/INTERVIEW_NOTES.md`

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

### 常见错误

#### `ModuleNotFoundError: No module named 'app'`

```bash
PYTHONPATH=. python scripts/retrieval_eval.py
```

#### `KeyError: expected_document_id`

说明 Golden Dataset 存在旧 Schema。

#### `IndentationError`

不要在函数外拼接带缩进的片段。使用完整函数或完整文件替换。

## Day 6：可信问答

### 启动服务

```bash
docker compose up -d
alembic upgrade head
uvicorn app.main:app --reload
```

### Swagger 验证

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

### 首次请求较慢

第一次请求会加载 Sentence Transformer 模型。这属于正常冷启动。

## Day 7：回答质量评测与文档删除

### 应用数据库迁移

```bash
alembic upgrade head
alembic current
```

确认当前数据库包含 Document 的 `deleted_at` 字段。

### 删除文档

Swagger：

```text
DELETE /documents/{document_id}
```

需要提供：

```text
document_id=<目标 Document ID>
workspace_id=<目标 Workspace ID>
```

删除成功后：

- PostgreSQL 中 `deleted_at` 非空。
- 该 Document 不再参与回答和检索。
- Qdrant 中对应 Points 执行 Best-effort Cleanup。

### 运行回答评测

从项目根目录执行：

```bash
PYTHONPATH=. python scripts/answer_eval.py \
  --output eval/answer_results_after_entity_scope_fix.jsonl
```

快速检查结果：

```bash
python - <<'PY'
import json
from pathlib import Path

path = Path("eval/answer_results_after_entity_scope_fix.jsonl")

for line in path.read_text(encoding="utf-8").splitlines():
    row = json.loads(line)
    actual = row["actual"]
    print(
        row["case"]["id"],
        f"refused={actual['refused']}",
        f"citations={len(actual['citations'])}",
        f"answer={actual['answer_text']}",
    )
PY
```

删除后的三条无答案 Case 预期：

```text
answer-001 refused=True citations=0
answer-002 refused=True citations=0
answer-003 refused=True citations=0
```

### 删除后仍返回答案

依次判断：

1. Citation 是否来自已删除文档。
2. PostgreSQL 查询是否过滤 `deleted_at`。
3. 检索结果是否来自其他未删除文档。
4. 该未删除文档是否真的支持目标主体及询问属性。
5. 若只存在关键词相关性而没有主体关系，属于 Entity Scope Mismatch。

不要把“检索到了相关内容”直接等同于“证据足以回答”。

### Qdrant Cleanup 失败

当前设计允许 PostgreSQL 软删除成功、Qdrant Cleanup 失败。

原因：

- PostgreSQL 是事实来源。
- Retrieval 还会通过 PostgreSQL 状态过滤已删除文档。
- Cleanup 属于 Best-effort 操作。

应检查日志并重试清理。长期改进方案为 Outbox Pattern。

## Day 9：P1 集成验证与 Gate 证据

### 同步 Day 9 分支

```bash
git switch day9-p1-gate-evidence
git pull --ff-only origin day9-p1-gate-evidence
```

### 运行定向测试

```bash
pytest -q tests/scripts/test_answer_eval.py
pytest -q tests/integration/test_p1_document_rag_lifecycle.py
```

验证生命周期测试之后的依赖健康：

```bash
pytest -q \
  tests/integration/test_p1_document_rag_lifecycle.py \
  tests/test_health.py::test_dependencies_health
```

### 运行完整回归

```bash
pytest -q
git diff --check
```

Day 9 已验证结果：

```text
126 passed
git diff --check: clean
```

已知非阻塞警告：

```text
StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated
```

### 重复验证依赖健康

```bash
for i in 1 2 3; do
  echo "health run $i"
  pytest -q tests/test_health.py::test_dependencies_health || exit 1
done
```

三次均应通过。

### 运行 10 条无答案评测

评测数据和原始结果保留在本地 `eval/`，不提交 GitHub。

```bash
PYTHONPATH=. python scripts/answer_eval.py \
  --dataset eval/answer_golden.jsonl \
  --output eval/answer_results.jsonl \
  --retrieval-limit 5
```

预期汇总：

```text
cases: 10
runtime_errors: 0
answerable_cases: 0
evaluated_answerable_cases: 0
over_refusals: 0
over_refusal_rate: n/a
unanswerable_cases: 10
evaluated_unanswerable_cases: 10
correct_refusals: 10
incorrect_answers: 0
incorrect_answer_rate: 0.000000
```

注意：

- `over_refusal_rate: n/a` 不是 `0%`。
- 这 10 条只能证明无答案安全性，不能证明有答案质量。
- 运行错误必须单独报告，不能计入正确拒答。

### 检查本地评测结果

```bash
python - <<'PY'
import json
from pathlib import Path

path = Path("eval/answer_results.jsonl")

for line in path.read_text(encoding="utf-8").splitlines():
    row = json.loads(line)
    case = row["case"]
    actual = row["actual"]
    print(
        case["id"],
        f"refused={actual['refused']}",
        f"citations={len(actual['citations'])}",
        f"error={actual['error']}",
    )
PY
```

10 条 Case 均应为：

```text
refused=True citations=0 error=None
```

### Gate 状态来源

Day 10 Review 统一以 `docs/PROJECT_STATUS.md` 中的阶段状态、验收证据和未完成条件为准。实现过程与问题修复记录在 `docs/DEV_LOG.md`；执行命令只维护在本文件。

Day 9 不执行：

- Gate 结论由 Day 10 Review 给出。
- README 的阶段声明必须在 Gate 结论之后更新。
- 不关闭 P1 Milestone。
- 不合并 Draft PR。

### 生命周期测试后健康检查返回 503

先让失败断言输出 `/health/dependencies` 的响应体，确认具体失败依赖。若仅发生在异步集成测试之后，应检查全局 SQLAlchemy AsyncEngine 是否保留了绑定到旧事件循环的 asyncpg 连接。

Day 9 的处理是在测试清理阶段执行：

```python
await engine.dispose()
```

该处理只重置测试后的连接池，不改变生产请求逻辑。

## Day 10：P1 CONDITIONAL PASS

### 当前 Gate 状态

```text
P1 Gate: CONDITIONAL PASS
```

唯一待补条件：

```text
answerable=true 定量评测
├── 正常样本
├── 易混淆样本
├── answer correctness
├── citation support
└── over-refusal
```

OCR、Hybrid Retrieval 和 Reranker 不属于 P1 Gate 条件。

### 构建 answerable=true 数据集

每条样本至少包含：

```text
id
question
workspace_id
answerable=true
reference_answer
expected_document_names
acceptance_criteria
category
notes
```

样本应同时覆盖：

- 正常样本：证据直接、单一且清晰
- 易混淆样本：相似术语、相邻 Chunk、跨文档数字或主体容易混淆

### 运行评测

```bash
PYTHONPATH=. python scripts/answer_eval.py \
  --dataset eval/answer_golden.jsonl \
  --output eval/answer_results.jsonl \
  --retrieval-limit 5
```

运行错误必须单独保留，不得计算为正确回答、正确拒答或过度拒答。

### 逐条人工验收

对每个 `answerable=true` 结果记录：

```text
answer_correct=true|false
citation_supported=true|false
failure_type=<null 或明确类型>
notes=<判定依据>
```

通过样本至少满足：

- `refused=false`
- 答案正确且完整
- 没有文档外推测
- Citation 直接支持全部关键结论
- 没有错误或多余 Citation

### 汇总 Gate 条件证据

至少记录：

```text
answerable_cases
evaluated_answerable_cases
answer_correct_count / rate
citation_supported_count / rate
over_refusals / over_refusal_rate
runtime_errors
retained_failure_cases
```

条件关闭前：

- `docs/PROJECT_STATUS.md` 保持 `CONDITIONAL PASS`
- P1 Milestone 保持未关闭
- PR #2 保持未合并

完成数据集、运行结果和失败审查后，再决定是否更新为最终 `PASS`。

## Day 12：BM25 Retrieval 与 Retrieval Golden Integrity

### 运行 BM25 focused tests

```bash
pytest -q \
  tests/retrieval/test_bm25_tokenizer.py \
  tests/retrieval/test_bm25_retrieval_service.py \
  tests/integration/test_bm25_repository.py
```

### 运行 Dense / BM25 正式评测

统一从项目根目录使用 module 方式运行，避免 `scripts/` 入口导致 `app` import path 丢失：

```bash
python -m scripts.retrieval_eval --top-k 5
python -m scripts.bm25_retrieval_eval --top-k 5
```

当前 Day 12 正式 baseline：

```text
Dense  Recall@5=0.700000  MRR@5=0.500000  MISS=9
BM25   Recall@5=0.700000  MRR@5=0.567778  MISS=9
```

### Retrieval Benchmark 前置条件：Golden integrity

文档被删除、替换或重新切块后，不得直接复用旧指标。评测前至少确认：

```text
30 cases
30 valid expected_chunk_id
0 stale labels
```

合法目标 Chunk 必须满足：

```text
workspace_id == case.workspace_id
deleted_at IS NULL
Document.status IN (COMPLETED, PARTIAL)
```

若 stale：

1. 先确认知识是否仍存在于当前 corpus。
2. 若存在，人工选择新的权威 Chunk 并更新标签。
3. 若知识已移除，替换该 Golden 问题。
4. 不允许 Dense/BM25 自动用自己的 Top-1 给自己重新标注。
5. 修复后同时重跑所有要比较的 Retriever。

### BM25 集成测试后出现 asyncpg event-loop 错误

症状：后续生命周期或依赖健康测试出现 `RuntimeError` / `InterfaceError`，单独测试可能正常。

若新增异步集成测试使用全局 AsyncEngine，在测试清理阶段执行：

```python
await engine.dispose()
```

该操作只清理测试连接池，避免连接跨 pytest event loop 复用，不改变生产数据库逻辑。

### 完整回归

```bash
pytest -q
git diff --check
git status
```

Day 12 已验证 Full Test Suite：PASS。Starlette/httpx TestClient deprecation warning 为非阻塞警告。

## 通用常见问题

### `No module named fastapi`

```bash
python -m pip install -r requirements.txt
```

### `requirements.txt` 找不到

确认当前目录是项目根目录。

### 终端出现 `heredoc>`

Shell 正在等待多行输入结束标记。按 `Ctrl+C` 取消。

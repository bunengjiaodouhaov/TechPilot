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

## Day 14：Cross Encoder Reranker

### focused tests

```bash
pytest -q \
  tests/retrieval/test_hybrid_retrieval_service.py \
  tests/retrieval/test_reranker_contract.py \
  tests/retrieval/test_cross_encoder_reranker.py \
  tests/retrieval/test_reranking_service.py
```

### 真实 smoke

```bash
HF_HUB_OFFLINE=1 python -m scripts.reranker_smoke
```

### 30-case 正式评测

```bash
HF_HUB_OFFLINE=1 python -m scripts.reranker_retrieval_eval \
  --candidate-limit 20 \
  --rerank-depth 20 \
  --top-k 5 \
  --rrf-k 60
```

正式结果：

```text
Dense              Recall@5=0.700000 MRR@5=0.500000 MISS=9
BM25               Recall@5=0.700000 MRR@5=0.567778 MISS=9
Hybrid             Recall@5=0.766667 MRR@5=0.588333 MISS=7
Hybrid+Reranker    Recall@5=0.866667 MRR@5=0.766667 MISS=4
```

结果文件保存在 `.local/days/day14/`。

正式 Golden integrity：

```text
30/30 valid
legal_documents = 5
legal_chunks = 1153
dataset_sha256 = e65e8490ef8e23018673712b2e595c6779e842094bd49d5d433fc5641bcef7f5
corpus_snapshot_sha256 = 1d393523789b235bcfc1f821491bf86c5bcd29f47e04da7ebb85362b9ad81b0e
```

深度语义：

```text
candidate_limit = 每一路 Dense/BM25 的召回深度
rerank_depth    = RRF union 中进入 Cross Encoder 的深度
final_top_k     = Reranker 最终返回深度

final_top_k <= rerank_depth <= 2 * candidate_limit
```

## 通用常见问题

### `No module named fastapi`

```bash
python -m pip install -r requirements.txt
```

### `requirements.txt` 找不到

确认当前目录是项目根目录。

### 终端出现 `heredoc>`

Shell 正在等待多行输入结束标记。按 `Ctrl+C` 取消。

## Day 13：RRF Hybrid Retrieval

### Focused tests

```bash
pytest -q \
  tests/retrieval/test_rrf.py \
  tests/retrieval/test_hybrid_retrieval_service.py
```

### 正式 Hybrid Retrieval Evaluation

从项目根目录执行：

```bash
python -m scripts.hybrid_retrieval_eval \
  --candidate-limit 20 \
  --top-k 5 \
  --rrf-k 60
```

正式结果：

```text
Dense   Recall@5=0.700000  MRR@5=0.500000  MISS=9
BM25    Recall@5=0.700000  MRR@5=0.567778  MISS=9
Hybrid  Recall@5=0.766667  MRR@5=0.588333  MISS=7
```

正式配置：

```text
candidate_limit=20
top_k=5
rrf_k=60
```

### Golden integrity

`hybrid_retrieval_eval.py` 会先验证 Golden 目标是否仍属于当前 legal corpus。

当前正式快照：

```text
cases=30
valid=30
legal_documents=5
legal_chunks=1153
dataset_sha256=e65e8490ef8e23018673712b2e595c6779e842094bd49d5d433fc5641bcef7f5
corpus_snapshot_sha256=1d393523789b235bcfc1f821491bf86c5bcd29f47e04da7ebb85362b9ad81b0e
```

如果 integrity FAIL：

- 停止评测。
- 不调 Retriever。
- 不允许用 Retriever Top-1 自动重新标注 Golden。
- 先确认 Document / Chunk 是否仍属于当前 corpus，再人工修正标签或替换问题。

### 有边界实验

Day 13 已验证：

```bash
python -m scripts.hybrid_retrieval_eval \
  --candidate-limit 10 \
  --top-k 5 \
  --rrf-k 60 \
  --output eval/hybrid_retrieval_results_candidate10.jsonl

python -m scripts.hybrid_retrieval_eval \
  --candidate-limit 20 \
  --top-k 5 \
  --rrf-k 20 \
  --output eval/hybrid_retrieval_results_k20.jsonl

python -m scripts.hybrid_retrieval_eval \
  --candidate-limit 20 \
  --top-k 5 \
  --rrf-k 1 \
  --output eval/hybrid_retrieval_results_k1.jsonl
```

这些实验只用于解释 candidate depth 和 `k` 的行为，不应继续扩大成 Golden 参数网格搜索。

### 最终回归

```bash
pytest -q
git diff --check
git status --short
```

Day 13 已验证：

```text
154 passed
git diff --check: PASS
```

真实 Hybrid Service smoke 也已通过；生产 `AnswerService` 仍保持 Dense-only。
## Day 15：Evidence Verifier 验证

真实 DeepSeek smoke：

```bash
python -m scripts.evidence_verifier_smoke
```

预期：

```text
prompt_version: evidence-verifier-v2
EVIDENCE VERIFIER SMOKE: PASS (3/3)
```

正式 Evidence Verifier evaluation：

```bash
python -m scripts.evidence_verifier_eval \
  --dataset eval/evidence_verifier_golden.jsonl \
  --output eval/evidence_verifier_results.jsonl
```

当前 Day 15 reviewed local Golden 预期：

```text
cases: 6
runtime_errors: 0
state_correct: 6
state_accuracy: 1.000000
reason_exact_matches: 6
reason_exact_match_rate: 1.000000
sufficient: 2/2
insufficient: 3/3
conflicting: 1/1
prompt_version: evidence-verifier-v2
```

注意：

- `eval/evidence_verifier_golden.jsonl` 与 results 为本地评测资产；不要用 Verifier 输出自动重标 Golden。
- Evidence Eval 的 state 正确与 reason 正确分开统计。
- `insufficient` 只允许一个 primary reason。
- Formal eval 通过不等于 P2 Gate 通过；Day 17 才做 P2 Gate。

## Day 16：P2 Ablation 与 Trace Identity

正式汇总：
```bash
python -m scripts.p2_ablation_eval --evidence-results eval/evidence_verifier_results.jsonl
```
输出 `.local/days/day16/p2_ablation_summary.json`。Trace identity 位于 JSON 的 `trace` 对象。

最终回归：
```bash
pytest -q tests/scripts/test_p2_ablation_eval.py
pytest -q
git diff --check
git status --short
```
Day16：234 passed；`git diff --check` PASS。

## Day 18：P3 Repository / Harness Foundation 验证

### Focused tests

```bash
pytest -q tests/repository tests/harness
git diff --check
```

Day18 focused baseline：

```text
34 passed
git diff --check: PASS
```

### Full regression

```bash
pytest -q
git diff --check
git status --short --untracked-files=all
```

要求：

- Full pytest PASS。
- 允许既有 Starlette TestClient/httpx deprecation warning，但不得把新 warning 混入“既有问题”。
- Day18 新增文件在 commit 前保持可审计。
- `.env`、`.local/`、`eval/` 不得提交。

### Repository boundary smoke 关注点

- path 必须保持在 repository root。
- absolute / `..` escape 拒绝。
- symlink 拒绝。
- `.git/.venv/venv/node_modules/cache/generated` 不遍历。
- `.env/.env.*` 不可读取；`.env.example/.env.sample` 可作为模板读取。
- binary / oversized file 拒绝。
- traversal 顺序 deterministic。

### Harness contract smoke 关注点

- input/output 必须经过 Pydantic validation。
- READ/COMPUTE 为 v1 默认允许风险级别。
- WRITE/DESTRUCTIVE 必须由 ToolRuntime 拒绝。
- timeout / execution error / invalid output 使用结构化 `ToolResult.error_code`。
- tool-level truncation 必须与统一 `ToolResult.truncated` 语义一致。

## Day 19：EvidencePack / Repo Explorer 验证

### Focused / Harness validation

```bash
pytest -q \
  tests/harness/test_tool_runtime.py \
  tests/harness/test_evidence_pack.py \
  tests/repository/test_repo_explorer.py

pytest -q tests/repository tests/harness
```

### Full regression

```bash
pytest -q
git diff --check
git status --short --untracked-files=all
```

Day19 验收关注点：

- tool output `truncated=True` 必须同步到 `ToolResult.truncated`。
- RepoExplorer 只能经 ToolRegistry / ToolRuntime 调用 repository tools。
- search result 不能直接作为 `CodeEvidence`。
- `read_file` 返回的 authoritative path / content 必须与 candidate provenance 一致。
- truncation、AST parse error、tool failure、evidence limit、provenance mismatch 必须显式进入 EvidencePack issue metadata。
- provenance mismatch 的 suspect evidence 必须丢弃，而不是继续进入 EvidencePack。
- 不允许 shell / edit / git write / worktree modification。

### Day20 AgentEvent trace validation

```bash
pytest -q \
  tests/harness/test_agent_event.py \
  tests/harness/test_tool_runtime.py \
  tests/repository/test_repo_explorer.py

pytest -q tests/repository tests/harness
pytest -q
git diff --check
git status --short --untracked-files=all
```

重点验证：

- 同一调查中的 tool call / result / evidence handoff 共享 trace_id。
- TOOL_RESULT 能记录 success/failure、latency、truncated 和 error_code 摘要。
- trace payload 不复制完整 tool argument value。
- event sink 抛错时 ToolResult 仍按业务执行结果返回。
- EvidencePack 不被 trace_id 或 event state 污染。

## Day 21–24 Code RAG 验证

从项目根目录执行：

```bash
pytest -q tests/repository tests/harness tests/retrieval
pytest -q
git diff --check
git status --short --untracked-files=all
```

当前 Day 24 收尾记录：

```text
FULL_PYTEST=PASS
DIFF_CHECK=PASS
```

继续开发时必须保持：

- repository access 经过只读边界；
- retrieval / module result 只作为候选或静态线索；
- 最终 `CodeEvidence` 继续从 `read_file` 权威源码构造；
- 不提前开放 shell / edit / git-write；
- 静态 import/call clue 不夸大为完整运行时关系。

<!-- DAY37_5_PRODUCT_UI_START -->
## Product UI (Day37.5)

启动现有服务：

```bash
uvicorn app.main:app --reload
```

访问：

```text
Product UI: http://127.0.0.1:8000/
API docs:   http://127.0.0.1:8000/docs
```

Day37.5 focused verification：

```bash
pytest -q tests/api/test_frontend.py tests/api/test_workspaces_api.py
git diff --check
```

Workspace API smoke：

```text
GET    /workspaces
POST   /workspaces
DELETE /workspaces/{workspace_id}
```

注意：删除仍有 active documents 的 Workspace 会返回 `409`；先在 Knowledge Base 删除对应 source。

浏览器如果仍显示旧 Day37.5 CSS，执行一次 hard refresh（macOS Chrome/Safari 常用 `Cmd + Shift + R`）。
<!-- DAY37_5_PRODUCT_UI_END -->

<!-- P5_DAY38_RUNBOOK_20260824_START -->
## P5 Day38 / full regression validation

启动依赖：

```bash
docker compose up -d
alembic upgrade head
```

检查 Qdrant 直连（绕过系统 proxy）：

```bash
curl --noproxy '*' -fsS http://127.0.0.1:6333/healthz
```

P5 focused：

```bash
pytest -q tests/jd tests/job
```

全仓库：

```bash
pytest -q
git diff --check
```

P5 forbidden-coupling check：

```bash
grep -R \
  -nE "app\.harness|EvidencePack|CodeEvidence|app\.repository" \
  app/jd app/job || true
```

预期为空。

### localhost Qdrant / proxy troubleshooting

若容器正常但应用得到 `502 Bad Gateway`：

```bash
env | grep -Ei '^(http_proxy|https_proxy|all_proxy|no_proxy)=' || true
curl -v http://127.0.0.1:6333/healthz
curl --noproxy '*' -v http://127.0.0.1:6333/healthz
```

若 `--noproxy` 成功而默认请求失败，优先检查 proxy environment；不要先重建 Qdrant 数据。

### asyncpg cross-event-loop troubleshooting

若测试出现：

```text
Future attached to a different loop
```

检查是否有 sync `TestClient` dependency probe 使用 application pooled `AsyncEngine`。

当前设计：

```text
application DB traffic → normal pooled AsyncEngine
health PostgreSQL probe → independent NullPool engine
```

不要为了测试方便把整个生产 application engine 改成 `NullPool`。
<!-- P5_DAY38_RUNBOOK_20260824_END -->

<!-- TECHPILOT_JOB_INTELLIGENCE_RUNBOOK_START -->
## Job Intelligence closeout / transition runbook

Do not continue expanding recruitment-site adapters by default.

Known current local hygiene issue:

- the BOSS v9 apply script copied extension files and then stopped because `node` was unavailable;
- therefore the extension directory may contain partially applied/unvalidated v9 files.

Before the next code milestone:

```bash
cd ~/TechPilot
git status --short
git diff --check
```

Then identify and remove/restore only the incomplete v9 experiment before any commit.

Preserve validated P5 prototype work.

Do not claim Flow B/C PASS unless resume extraction E2E is rerun successfully.

Do not claim BOSS full-JD support based on listing captures.

Next-session entry criteria for AI Coding:

```text
competitor comparison complete
-> differentiated product thesis frozen
-> real-repository evaluation plan defined
-> only then implement
```

Git commit/push/merge/tag still requires explicit user authorization.
<!-- TECHPILOT_JOB_INTELLIGENCE_RUNBOOK_END -->

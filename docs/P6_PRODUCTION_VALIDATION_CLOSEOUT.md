# TechPilot P6 Production Boundary Hardening — Closeout

> Date: 2026-08-27  
> Branch: `p6-production-boundary-hardening`  
> Status: **P6 PRODUCTION VALIDATION PASS WITH EXPLICIT KNOWN LIMITATIONS**
>
> 本文是 P6 的当前事实来源，supersede 早期 Day17 `AnswerService = Dense-only` 的 production freeze，但不删除历史记录。旧文档中的 Dense-only / BGE reranker 表述应理解为历史阶段状态，不代表 2026-08-27 当前生产配置。

---

## 1. P6 目标与最终结论

P6 的目标不是再增加一个 Agent feature，而是验证 TechPilot 在真实多用户、并发、重试、部分失败和真实文档输入下是否具有明确的 production boundary。

最终完成的 production-facing 能力包括：

- JWT authentication 与 workspace membership authorization；
- cross-user / cross-workspace fail-closed isolation；
- conversation optimistic concurrency / CAS；
- request idempotency replay、payload conflict 与 failure retry；
- request-scope transaction release，避免慢 LLM 调用长期占有数据库事务；
- DOCX ingestion、结构化 heading/table extraction 与 OOXML ZIP preflight；
- Hybrid RRF + multilingual CrossEncoder production retrieval；
- Evidence Verifier 驱动的 bounded second-chance structural recovery；
- 跨章节 `±1` boundary bridge，同时保持真实 citation provenance；
- provider retry exhaustion / partial failure / indexing compensation 的 failure semantics；
- FAILED/PENDING document 在 PostgreSQL authoritative answer boundary 上 fail-closed。

P6 的核心结论不是“不会失败”，而是：

```text
failure is explicit
state transitions are bounded
unsafe stale/partial state is not silently promoted to valid evidence
```

---

## 2. Validation matrix

| Validation | Result | Evidence level | Notes |
| --- | --- | --- | --- |
| P6-1 cross-user workspace isolation | PASS | live HTTP + DB state | unauthorized list/read/answer/upload fail-closed；真实 document destructive delete 未单独保留一条 live attack transcript，不夸大该单点证据 |
| P6-3 conversation CAS concurrency | PASS | live concurrent HTTP | one request 200, stale concurrent writer 409；失败请求没有写入 stale/half turns |
| P6-4 idempotency | PASS | live HTTP | identical same-key replay exact response；same key + different payload 409；replay latency约 10ms |
| DOCX parser / persist / index | PASS | tests + live uploaded DOCX | 70 chunks persisted/indexed；heading/table structure retained；images currently not interpreted |
| multilingual reranker selection | PASS | strict 30-case P2 regression + DOCX eval | selected on quality/regression/latency trade-off, not on one query |
| DOCX semantic-gap `/answers` | PASS | real HTTP E2E | exact permission query returns `refused=false` with grounded Q38/Q39/Q40/Q44 citations |
| P6-5A provider retry exhaustion | PASS | deterministic regression | timeout / retryable provider failures bounded；not a live external-provider outage injection |
| P6-5B idempotency failure retry | PASS | real PostgreSQL live probe | `PROCESSING -> FAILED -> same-key PROCESSING -> COMPLETED` |
| P6-5C conversation + failure composition | PASS | targeted API/integration regression | provider failure no half-turn；CAS conflict returns idempotency to retriable FAILED state |
| P6-5D post-commit ingestion failure | PASS | real PostgreSQL + simulated indexing failure | persisted chunks remain physically present but document becomes FAILED and is non-searchable |
| full regression | PASS | local full pytest | user-confirmed 100% / PASS |
| `git diff --check` | PASS | local | user-confirmed |

---

## 3. Authentication / authorization boundary

### 3.1 Authentication

Authentication uses signed access tokens and resolves the request to an `AuthPrincipal(user_id, email, ...)`.

Security-relevant points:

- bearer token / HttpOnly cookie are request credentials；
- invalid / expired token returns 401；
- production startup refuses the default development signing secret；
- authentication only answers “who is the user”，it does not grant workspace access。

### 3.2 Authorization

Workspace access is checked through membership data rather than trusting `request.workspace_id`.

```text
request.workspace_id
        ↓
WorkspaceAuthorizer.require_access(user_id, workspace_id)
        ↓
workspace_member
        ↓
allowed / 404 fail-closed
```

Unauthorized workspace access intentionally returns 404 in the main isolation path so the API does not disclose whether the target workspace exists.

### 3.3 Live isolation evidence

A non-owner user attempting to use another user's workspace was unable to:

- see the workspace in workspace listing；
- answer against the workspace；
- upload a document into the workspace。

These paths returned fail-closed behavior rather than accidentally trusting caller-supplied IDs.

Do not overstate the evidence: the destructive delete path against the actual real document was not separately retained as a live attack transcript, although the route uses the same workspace authorization boundary and automated coverage exists.

---

## 4. Conversation concurrency / CAS

The `/answers` path reads conversation state and version, releases database reads before slow answer generation, then appends the user+assistant exchange only if the conversation version is still unchanged.

```text
load conversation(version=N)
        ↓
release read transaction
        ↓
retrieval / verifier / LLM
        ↓
UPDATE conversation
WHERE id=? AND version=N
        ↓
rowcount == 1 ? append both turns : 409
```

Important invariant:

> Version bump and the two conversation turns are staged in the same caller transaction. A stale writer does not write one half of an exchange.

Live concurrency test:

- two requests generated from the same conversation version；
- one request completed；
- the other returned 409 `conversation changed while the answer was generating`；
- the conversation contained exactly the successful request's two turns；
- no stale user turn or assistant half-turn was left behind。

This is optimistic concurrency control, not distributed locking.

---

## 5. Idempotency semantics

Idempotency records are scoped by:

```text
user_id
workspace_id
operation
Idempotency-Key
```

Request payload is bound by a deterministic SHA-256 request hash.

State machine:

```text
new key
  -> PROCESSING
  -> COMPLETED(response/status)

provider/business failure
  -> FAILED
  -> same request + same key may re-enter PROCESSING

same key + different request hash
  -> 409 conflict

same key while PROCESSING
  -> 409 already processing
```

Live evidence:

- same key + identical `/answers` request replayed byte-equivalent JSON response；
- replay was approximately 10ms, demonstrating stored response replay instead of a second generation；
- same key + different request returned 409；
- failure-recovery DB probe validated `FAILED -> retry -> COMPLETED` using the same idempotency record。

### 5.1 What this does NOT guarantee

Do **not** describe the current upload path as exactly-once.

`IngestionService` intentionally has multiple transaction boundaries:

1. persist PENDING Document；
2. persist chunks + final PostgreSQL document state；
3. vector indexing；
4. endpoint idempotency completion。

A process crash after committed Document/Chunks but before endpoint idempotency completion can leave a narrow commit/completion window. The correct claim is:

> idempotent replay/deduplication with a known ingestion commit-completion crash window, not exactly-once ingestion.

A future stronger guarantee would require a transactional outbox / operation ledger / resumable ingestion design rather than wording the current implementation more strongly.

---

## 6. DOCX ingestion

P6 added `.docx` as a first-class ingestion type alongside PDF/Markdown.

### 6.1 Parser

The DOCX parser uses `python-docx` and preserves body order across:

- paragraphs；
- tables；
- heading hierarchy (`Heading 1..6`)；
- title fallback metadata。

The resulting chunks preserve a structural `section` path such as:

```text
五、MCP Gateway v2：第三核心模块
  > Q38. 为什么工具要分 READ、WRITE、DANGEROUS？
```

### 6.2 OOXML preflight

Because DOCX is an OOXML ZIP package, upload validation includes bounded archive preflight before parser expansion:

- max entry count；
- max total uncompressed bytes；
- max per-entry bytes；
- compression-ratio guard；
- required `[Content_Types].xml` and `word/document.xml`；
- invalid/encrypted ZIP rejection。

This prevents treating arbitrary ZIP bombs as harmless office documents.

### 6.3 Current limitations

Current DOCX support does not claim:

- image/multimodal understanding；
- header/footer extraction；
- nested-table semantic reconstruction；
- exact page numbers, because DOCX is flow-layout content rather than a stable page-oriented format。

The citation therefore correctly uses section provenance with `page_start/page_end = null` for the validated DOCX example.

---

## 7. Production retrieval change: Dense-only -> Hybrid + multilingual reranker

### 7.1 Why the Day17 freeze changed

Day17 correctly froze interactive production answering to Dense-only because the then-evaluated BGE CrossEncoder added roughly multi-second rerank latency and did not fix the main candidate-generation failures.

P6 reopened the decision only after:

- later 400-case retrieval backfill showed Hybrid + CrossEncoder as a much stronger general candidate；
- real DOCX mixed Chinese/English workload exposed a concrete semantic-gap failure；
- three rerankers were compared on the same strict 30-case P2 Golden in the same environment。

### 7.2 Strict 30-case reranker comparison

#### Old English default

`cross-encoder/ms-marco-MiniLM-L-6-v2`

```text
Hybrid+reranker Recall@5 = 0.666667
MRR@5                   = 0.578889
rescues                 = 2
regressions             = 5
rerank inference mean   ≈ 147 ms
```

The model was fast but performed poorly on the mixed Chinese/English corpus.

#### Multilingual MiniLM

`cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`

```text
Hybrid+reranker Recall@5 = 0.866667
MRR@5                   = 0.740000
rescues                 = 3
regressions             = 0
rerank inference mean   ≈ 253 ms
reranked total mean     ≈ 889 ms
```

#### BGE reranker

`BAAI/bge-reranker-v2-m3`

```text
Hybrid+reranker Recall@5 = 0.866667
MRR@5                   = 0.766667
rescues                 = 3
regressions             = 0
rerank inference mean   ≈ 2320 ms
reranked total mean     ≈ 3025 ms
```

BGE had the best offline MRR, but multilingual MiniLM had identical Recall@5 and zero regressions while rerank inference was roughly 9x faster. The production default therefore changed to multilingual MiniLM.

This is a quality/latency trade-off decision, not a claim that multilingual MiniLM is universally the strongest reranker.

---

## 8. DOCX semantic-gap failure: investigation and final recovery design

### 8.1 Real failure

Uploaded real interview DOCX contained the required evidence, but the production request:

```text
EnterpriseOps项目中权限校验是如何实现的？
```

initially returned:

```json
{
  "answer": "现有文档中没有足够证据回答这个问题。",
  "citations": [],
  "refused": true
}
```

This was not a parser/indexing failure. PostgreSQL contained 70 chunks and BM25/Qdrant could retrieve the document.

### 8.2 Gold evidence

The required implementation spans multiple chunks/mechanisms:

- Q38 / chunk_index 39: `READ / WRITE / DANGEROUS`, unknown tool defaults to DANGEROUS；
- Q40 / chunk_index 41: Tool Schema validation before server call；
- Q44 / chunk_index 45: HITL / approval is a hard execution-boundary control；
- Q39 also explains dynamic discovery and retained permission metadata。

The question is semantically broad: “权限校验” does not literally contain all internal vocabulary above.

### 8.3 Experiments rejected before production

The following were evaluated and **not** blindly shipped:

1. **larger rerank depth** — admission improved but did not reliably put all needed evidence in top context；
2. **manual flat query expansion** — strong result, but oracle/manual vocabulary is not a production solution；
3. **manual multi-query decomposition** — strong coverage, still oracle-like；
4. **automatic LLM decomposition** — failed provisional quality gates and added about 1.36s query-generation mean latency；
5. **grounded PRF query generation** — promising but added an extra LLM hop and still missed part of the exact permission evidence；
6. **structural rerank expansion** — improved candidate coverage/Recall@20 but did not improve top-5 practical outcome enough to justify shipping as the default retrieval path。

This matters because the final production change was selected from failure attribution, not by moving whichever experiment produced the best one-off answer into production.

### 8.4 Final production recovery

The final design is verifier-driven and only runs after the first evidence verification returns `INSUFFICIENT`.

```text
Hybrid + multilingual reranker
        ↓
first Context / Evidence Verifier
        │
        ├─ SUFFICIENT  -> generate
        ├─ CONFLICTING -> fail closed / no recovery
        └─ INSUFFICIENT
                 ↓
          second-chance structural recovery
                 ↓
          second Evidence Verifier
                 │
                 ├─ SUFFICIENT -> generate
                 └─ else       -> safe refusal
```

Recovery policy:

- take a broader top-N only as structural anchors；
- group anchors by authoritative `document_id + parent section`；
- prefer parent sections not already represented in first-pass context；
- exclude first-pass chunks and top-N anchors from recovery additions so the budget is used for new evidence；
- load authoritative siblings from PostgreSQL；
- allow a tightly bounded cross-section bridge only for an immediate `chunk_index ±1` neighbor of a selected anchor；
- preserve the neighbor's **real section metadata** in the final citation。

### 8.5 Two real bugs found in the recovery selector

#### Bug 1 — recovery budget duplication

Initial production selector ranked the already-present “项目总览” section first and allowed top-N anchors to be added again. The 12-addition budget was mostly consumed by duplicate/generic evidence.

Fix:

```text
novel parent sections first
+ exclude all existing first-pass / top-N anchors
```

#### Bug 2 — mechanism crosses a heading boundary

Q44/HITL was not under the MCP heading. Its real section was:

```text
六、HITL / Reliability / Checkpoint > Q44...
```

while the last MCP anchor Q43 was chunk_index 44 and Q44 was chunk_index 45.

The parser was correct. The recovery assumption “one mechanism == one parent section” was wrong.

Fix: a bounded `±1` boundary bridge. The system does **not** rewrite Q44's section to MCP; citation retains the real HITL section.

### 8.6 Final live E2E

The same exact user query then returned `refused=false` with grounded citations including:

- Q44 HITL hard approval boundary；
- Q38 permission risk classification and unknown-default-DANGEROUS；
- Q39 dynamic discovery/permission metadata；
- Q40 schema validation before external tool execution。

This closes the specific DOCX semantic-gap live gate.

---

## 9. Evidence / generation wording boundary

The final generated answer said, in simplified form, “权限校验通过 MCP Gateway 实现”. The citations support the mechanisms, but the most precise architecture wording is:

```text
MCP Gateway
  -> tool discovery
  -> permission classification
  -> schema validation
  -> approval metadata

Runtime / approval execution boundary
  -> hard-enforces approval for WRITE / DANGEROUS
  -> no side effect before approval
```

Interview/documentation claims should use this more precise formulation. Prompt-level “先问我” is only a soft behavioral instruction and is not the hard security boundary.

---

## 10. P6-5 Failure Recovery / Partial Failure

### 10.1 Provider retry

Answer provider wrappers retry only bounded transient classes such as timeout/network and selected retryable HTTP statuses. Non-retryable 4xx fail fast. Structured-output repair is separately bounded.

P6 added regression coverage for retry exhaustion so a permanently failing provider cannot loop indefinitely.

Important distinction from P4 Agent control:

- P4 Research control owns Agent decision retry semantics；
- `/answers` provider wrapper owns bounded request-provider retry for the answer/evidence providers。

Do not merge these into one generic “system retries everything” claim.

### 10.2 Answer failure + idempotency

If answer generation fails after an idempotency record was created:

```text
PROCESSING
  -> failure handler
  -> FAILED
```

The same key + same request can later re-enter PROCESSING instead of being permanently stuck.

Targeted API regression covers provider failure and retry composition. The real PostgreSQL live probe also validated the FAILED/retry/completion state machine.

### 10.3 Conversation failure

Slow answer generation occurs before appending the conversation exchange. Therefore provider failure should not leave:

- user-only turn；
- assistant-only turn；
- version increment without the complete exchange。

CAS conflict similarly rolls back stale writes and returns an explicit 409.

### 10.4 Ingestion partial failure

`IngestionService` persists document/chunks before vector indexing. This creates an intentional source-of-truth boundary:

```text
PostgreSQL commit
        ↓
Qdrant indexing
```

If vector indexing fails, the service marks the document `FAILED` and propagates the error.

Indexing itself has document-level compensation: if a multi-batch Qdrant write partially succeeds, it attempts to delete every vector point for that document before propagating the original failure.

### 10.5 Failure discovered during P6-5

A stronger edge case exists:

```text
some Qdrant points written
        ↓
indexing error
        ↓
Qdrant compensation delete ALSO fails
```

Qdrant could temporarily retain orphan points for a document that PostgreSQL has marked FAILED.

BM25 already excluded FAILED/PENDING documents, but the PostgreSQL authoritative `ChunkRepository` used by Answer/Reranker previously filtered workspace/deletion without requiring a searchable document status.

Fix:

> `ChunkRepository.get_by_ids` and structural recovery reads now only accept `Document.status IN (COMPLETED, PARTIAL)`.

Therefore an orphan Qdrant point cannot be promoted to authoritative answer evidence if its PostgreSQL document is FAILED/PENDING.

This is a fail-closed defense-in-depth boundary; it does not eliminate the need for eventual orphan-index cleanup.

### 10.6 Live DB failure injection

The live P6 probe uses real PostgreSQL and a deliberately failing indexing service:

```text
Document PENDING committed
→ chunks/final state committed
→ simulated post-commit indexing failure
→ Document FAILED
→ persisted chunks still physically present
→ ChunkRepository returns none
→ BM25 corpus excludes them
→ probe data cleaned
```

Result: PASS.

This is a real-DB fault injection, not a claim that an actual Qdrant process crash was externally induced during the run.

---

## 11. Current production answering path

As of P6 closeout, the production answer path is:

```text
POST /answers
  ↓
Authentication
  ↓
Workspace authorization
  ↓
Optional idempotency begin
  ↓
Optional conversation snapshot/version
  ↓
Hybrid Retrieval
  ├─ multilingual-e5-base Dense
  └─ BM25
        ↓
      RRF
        ↓
CrossEncoder reranker
  model = cross-encoder/mmarco-mMiniLMv2-L12-H384-v1
        ↓
PostgreSQL authoritative searchable chunks
        ↓
Context Builder
        ↓
Evidence Verifier #1
  ├─ sufficient -> generate
  ├─ conflicting -> refuse
  └─ insufficient
        ↓
bounded structural recovery
  ├─ novel parent-section preference
  ├─ exclude already-seen anchors
  └─ immediate ±1 cross-section bridge
        ↓
Evidence Verifier #2
  ├─ sufficient -> verified sources only -> generator
  └─ otherwise -> refuse
        ↓
server-built citations
        ↓
conversation CAS append + idempotency completion
```

This section supersedes older production documents that still say `AnswerService -> DenseRetrievalService` only.

---

## 12. Known limitations after P6

P6 PASS does not mean production-scale completeness.

Known limitations intentionally retained:

1. **Idempotency is not exactly-once ingestion.** There is a known commit/completion crash window.
2. **Qdrant orphan cleanup is compensating/best-effort.** PostgreSQL fail-closed status filtering prevents invalid answer evidence, but a durable outbox/reconciler is still a future reliability improvement.
3. **DOCX multimodal content is not understood.** Embedded images/diagrams are not yet interpreted.
4. **DOCX section recovery is bounded structural adjacency, not a general semantic graph.** `±1` bridge is deliberately narrow.
5. **Evidence recovery adds work only on first-pass insufficiency.** It is not free and should be measured under load.
6. **Provider outage validation is partly deterministic fault injection.** P6 did not intentionally take the real DeepSeek provider offline for a production outage test.
7. **Cross-user destructive delete on the real document was not retained as a separate live attack transcript.** Other workspace-isolation live paths plus automated authorization coverage passed.
8. **High concurrency / P95/P99 / saturation / queueing behavior is not closed by P6.** This is the next system-validation layer.
9. **Open registration / rate limiting / lockout / explicit CSRF hardening are not claimed as complete production IAM.**

---

## 13. Interview framing

### Q1. P6 主要解决了什么？

> P6 不是继续堆 Agent 功能，而是把已有系统放到真实 production boundary 下验证：用户/Workspace 隔离、并发 CAS、幂等、慢 LLM 事务边界、DOCX、真实检索失败、provider failure 和 ingestion partial failure。最终目标是系统即使失败也不能留下含糊的半状态或把无效数据提升成证据。

### Q2. Authentication 和 authorization 怎么分？

> JWT 只证明“你是谁”；workspace membership 决定“你能访问哪个 workspace”。接口不会信任客户端传来的 workspace_id，而是用当前 user_id 去查 membership，未授权主路径 fail-closed 为 404。

### Q3. 为什么 Conversation 不直接加数据库锁？

> LLM 调用可能很慢，如果事务/锁跨 provider call 持有，会放大连接占用和 contention。我先读取 conversation version，释放 read transaction，生成完成后用 `UPDATE ... WHERE version=expected` 做 CAS；失败返回 409，并且 version bump + 两个 turns 在同一事务里。

### Q4. Idempotency 能不能说 exactly-once？

> 不能。Answer replay/deduplication 已验证，但 ingestion 有 PG commit、vector indexing、endpoint completion 多个边界。进程在 commit 后、idempotency completion 前崩溃存在窄窗口，所以我只说 idempotent replay with a known crash window，不说 exactly-once。

### Q5. 为什么换 reranker？

> 旧 `ms-marco-MiniLM` 在 30-case 混合中文/英文 Golden 上有 5 个 regression，Recall@5 只有 0.667。Multilingual MiniLM 达到 0.867、0 regression；BGE MRR 略高，但 inference 大约 2.32s 对 0.253s。生产选择 multilingual 是相同 Recall 下明显更好的 latency trade-off。

### Q6. DOCX 那个权限问题为什么一开始答不出来？

> Parser/indexing 都正常，真正问题是一个宽泛用户词“权限校验”对应文档中的多个内部机制：风险分类、schema validation、approval/HITL，而且证据跨章节。RRF/top-k 更偏泛化项目 chunk，单轮 context 不足，所以 verifier 正确拒答。

### Q7. 为什么不用 LLM query rewrite 直接解决？

> 我实际做了 automatic decomposition 和 grounded PRF eval。Automatic decomposition 没过质量 gate，还增加约 1.36s；grounded PRF 有改善但仍有部分 evidence miss，并增加额外 LLM hop。最后选择 verifier-insufficient 才触发的 deterministic bounded structural recovery，延迟/风险更可控。

### Q8. `±1` boundary bridge 会不会是为 Q44 写特例？

> 没有写 Q44、HITL 或 EnterpriseOps 关键词。规则是：只对已被检索选中的高相关 parent group 的 anchor，允许同 document 中立即相邻一个 chunk 跨 heading；metadata 仍取 PostgreSQL 真实 section。它解决的是“机制可能恰好跨 heading 边界”的结构问题，而不是某一道题。

### Q9. P6-5 最关键的 failure bug 是什么？

> Qdrant partial write 后如果 compensation delete 也失败，会残留 orphan vector。BM25 会排除 FAILED document，但 Answer/Reranker 的 authoritative ChunkRepository 原来没有 status filter。我把 authoritative load 也收紧到 COMPLETED/PARTIAL，因此即使索引层残留脏 point，也不能变成合法 Evidence。

### Q10. 现在权限控制到底在哪里？

最精确的回答是：

> MCP Gateway 负责工具 discovery、permission classification、schema validation 和 approval metadata；WRITE/DANGEROUS 的真正硬控制发生在 runtime/tool execution boundary，批准前不执行。Prompt 里的“先问我”只是软约束，不是 security boundary。

---

## 14. P6 code/validation assets

Production-relevant modules include:

- `app/auth/*`
- `app/conversations/concurrency.py`
- `app/api/answers.py`
- `app/auth/idempotency.py`
- `app/answering/answer_service.py`
- `app/answering/recovery_answer_service.py`
- `app/answering/chunk_repository.py`
- `app/retrieval/*`
- `app/ingestion/parsers/docx.py`
- `app/ingestion/service.py`
- `app/retrieval/indexing_service.py`

Important P6 evaluation / diagnostics assets include:

- `evals/retrieval/docx_semantic_gap_cases.jsonl`
- `scripts/docx_semantic_gap_eval.py`
- `scripts/docx_semantic_gap_auto_query_eval.py`
- `scripts/docx_semantic_gap_grounded_query_eval.py`
- `scripts/docx_semantic_gap_structural_eval.py`
- `scripts/answer_recovery_trace.py`
- `scripts/p6_failure_recovery_live.py`
- `tests/p6/test_answer_failure_recovery.py`
- `tests/answering/test_recovery_boundary_policy.py`
- `tests/answering/test_provider_retry.py`
- `tests/retrieval/test_indexing_service_batching.py`

Evaluation-only scripts are evidence/diagnostic assets and must not be described as production query rewrite behavior.

---

## 15. Closeout decision

P6 production-boundary hardening is closed as:

```text
Authentication / authorization         PASS
Workspace isolation                    PASS
Conversation CAS                       PASS
Idempotent replay/conflict             PASS
DOCX ingestion                         PASS
Multilingual production reranker       PASS
Semantic-gap bounded recovery          PASS
Citation provenance                    PASS
Failure recovery / partial failure     PASS
Full regression                        PASS

Known exactly-once claim               NOT MADE
High-concurrency/load validation       NEXT
```

Next system validation should move to concurrency/load/resilience rather than reopening the already-closed reranker or DOCX semantic-gap experiments without a new regression.

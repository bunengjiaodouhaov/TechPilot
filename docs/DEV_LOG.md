# TechPilot DEV_LOG

## Historical Development Log

> Day1–Day38 的详细历史仍保留在 Git history；本文件从 P6 开始继续记录 production-facing closeout。P6 文档修改不会把历史阶段的“当时判断”改写成“从来没有发生过”。历史关键基线仍可通过 commit history / earlier versions 回查。

## P0–P5 Historical Checkpoint Summary

### P0–P2 Document RAG

- FastAPI / PostgreSQL / Redis / Qdrant / SQLAlchemy / Alembic 工程骨架完成。
- Markdown / PDF ingestion、structure-aware chunk、Dense retrieval、BM25、RRF Hybrid、CrossEncoder、Evidence Verifier、Citation binding 完成。
- Day17 曾基于当时 7-case production A/B 将 v0.2-rag production retrieval 冻结为 Dense-only；这是当时真实决策，P6 后已 supersede。
- Evaluation Backfill 最终 Document Retrieval 400-case：Hybrid+CrossEncoder Recall@5 `83.4%`、MRR@5 `72.6%`、nDCG@5 `74.9%`、Coverage `84.7%`、P95 ~`867ms`。
- Answer/Evidence 180-case：146 answered；assistant audit full correct `140/146 = 95.89%`；20-case source-binding adversarial set Verifier v2 false-answer `5%`。
- OCR closed with known layout/table limitation。

### P3 Code RAG

- RepositoryReadBoundary / ToolRuntime / ToolRegistry / CodeEvidence / EvidencePack / RepoExplorer。
- Keyword / Dense / Hybrid code retrieval；AST module/import/symbol/static-call structural clue；structural snapshot/index。
- 50-case TechPilot held-out、Buku / yewtube external robustness；P3 FINAL PASS WITH KNOWN LIMITATIONS。
- 核心边界：candidate discovery 与 authoritative `read_file -> CodeEvidence` 分离。

### P4 Research Agent

- Thin Agent / Thick Harness；LangGraph bounded control；Unified Semantic Reasoner；Workflow / Light / Research execution profiles。
- provider failure ownership、retry budget、max_steps final semantic decision、source role、DecisionReportFinalizer。
- 36-case backfill 后 unsupported production claims `6/6` safe、false completion `0`、provenance `100%`。
- 保留 multi-obligation decomposition / obligation persistence / semantic planning limitation。

### P5 Job Intelligence

- JD structured extraction / Pydantic gate / bounded repair / exact evidence span；provider-neutral job discovery / matching foundation。
- 真实 Nowcoder / Shixiseng Flow A 验证；BOSS full-JD source coverage 与 Resume Flow B/C 未关闭。
- 最终状态：**CLOSED AS A BUSINESS PROTOTYPE — NOT A PRODUCT GATE PASS**。

---

# P6 Production Boundary Hardening / Production Validation

## P6-0：目标冻结

P6 不继续堆新的 Agent feature，目标转为 production boundary：

```text
authentication
authorization / tenant isolation
concurrency
idempotency
transaction lifetime
partial failure / recovery
real DOCX ingestion
production retrieval composition
```

原则：

- code implemented ≠ live validated；
- test pass ≠ product/production gate pass；
- fail-closed correctness 与 operational cleanup 分开；
- 不把 request `workspace_id` 当授权；
- 不把 idempotency 包装成 exactly-once；
- 不把单个并发竞争 case 包装成 load test。

## P6-1：Authentication / Authorization / Isolation

### 实现

- 新增 user / workspace membership security model；
- JWT / Cookie → `AuthPrincipal`；
- `WorkspaceAuthorizer.require_access(...)` 统一 workspace-scoped authorization；
- new workspace owner membership 同事务建立；
- Workspace list/create、documents、answers、conversations、product memory 等接入 auth boundary；
- repository/cache manifest 增加 owner identity；
- migration 给历史 demo workspace 建立兼容 membership；
- production 环境拒绝 default auth secret。

### 认证

- PBKDF2-SHA256；
- 310k iterations；
- random salt；
- constant-time compare；
- JWT HS256；
- Bearer / HttpOnly cookie；
- `/auth/register`、`/auth/token`、`/auth/logout`、`/auth/me`。

### Live validation

User B owns workspace `179`：

- User A `POST /answers` workspace179 → `404 workspace not found`；
- User A `POST /documents/upload` workspace179 → `404 workspace not found`；
- User A `GET /workspaces` 不包含179。

真实 workspace179 document：

```text
document_id = 530
name = EnterpriseOps_项目面试八股_高频详解版_最终更新版.docx
status = COMPLETED
chunk_count = 70
```

### 边界

此前 unauthorized delete probe 使用过不存在的 document id，因此对真实 document530 的 destructive delete 没有独立 live attack evidence。不能声称“所有 destructive endpoints 都逐条 live 验证”。

P6-1：主体隔离 **PASS WITH THIS EVIDENCE LIMIT**。

## P6-2：Transaction Boundary / Auth Regression

### 关键实现

`/answers`：

```text
auth + authorization
→ load optional conversation/version/history
→ idempotency begin
→ commit/release session transaction
→ retrieval/verifier/LLM slow path
→ final CAS / idempotency write
```

不把 async DB transaction 挂在 provider 慢调用上。

### Regression repairs

- PyJWT dependency；
- FastAPI Annotated/Header compatibility；
- historical tests 使用 dependency override，而不是把 auth 关闭；
- asyncpg cross-event-loop fixture cleanup；
- registration UI / auth API compatibility。

## P6-3：Conversation CAS

真实 workspace179 / conversation11：并发两次 `/answers`。

结果：

```text
request A = 200
request B = 409
```

409 detail：

```text
conversation changed while the answer was generating; reload the latest history and retry
```

最终 conversation 只写入成功请求两条 turn：

- user turn；
- assistant turn。

失败并发请求无 stale/half turn。

结论：optimistic CAS live **PASS**。

## P6-4：Idempotency

### Contract

scope：

```text
user_id + workspace_id + operation + key + request_hash
```

states：

```text
PROCESSING / COMPLETED / FAILED
```

### Live

- same key + identical request → identical stored response replay；
- replay latency ~`0.010055s`；
- same key + different request → `409 Idempotency-Key was already used for a different request`。

request hash：typed request JSON stable serialization + SHA256。

### 不夸大

不是 exactly-once。

Ingestion 存在：

```text
PG business commit
→ endpoint idempotency completion
```

之间的 narrow crash window。

P6-4：**PASS WITH KNOWN CRASH WINDOW**。

---

# P6 DOCX / Production Retrieval

## DOCX support

新增 `.docx` production ingestion：

- dependency `python-docx`；
- body order paragraph/table；
- Heading 1–6 hierarchy；
- structure-aware chunking；
- MIME/extension routing；
- UI upload/progress/badge；
- OOXML ZIP preflight。

OOXML preflight：

- max 4096 entries；
- max 128MB total uncompressed；
- max 64MB per entry；
- compression ratio guard 200 for >=1KB；
- requires `[Content_Types].xml` / `word/document.xml`；
- rejects bad/encrypted ZIP。

Known limits：images uninterpreted、headers/footers not parsed、nested tables not full semantic reconstruction、`.doc/.rtf` unsupported。

Full DOCX regression：PASS。

## 首次真实 DOCX Answer Failure

Question：

```text
EnterpriseOps项目中权限校验是如何实现的？
```

最初 production response：

```json
{
  "answer": "现有文档中没有足够证据回答这个问题。",
  "citations": [],
  "refused": true
}
```

检查 workspace179：70 chunks 全部已写 PG/index；不是 parser/indexing 故障。

权威关键证据：

```text
chunk39 / Q38  READ / WRITE / DANGEROUS, unknown → DANGEROUS
chunk41 / Q40  Tool Schema validation before server call
chunk45 / Q44  HITL approval hard execution boundary
```

结论：semantic retrieval/evidence admission gap。

## Reranker model revalidation

旧 production default：

```text
cross-encoder/ms-marco-MiniLM-L-6-v2
```

strict P2 30-case：

```text
Recall@5 = .666667
MRR@5    = .578889
MISS     = 10
rescues  = 2
regressions = 5
rerank inference mean ≈147ms
```

multilingual：

```text
cross-encoder/mmarco-mMiniLMv2-L12-H384-v1
Recall@5 = .866667
MRR@5    = .740000
MISS     = 4
rescues  = 3
regressions = 0
rerank inference mean ≈253ms
```

BGE：

```text
BAAI/bge-reranker-v2-m3
Recall@5 = .866667
MRR@5    = .766667
MISS     = 4
rescues  = 3
regressions = 0
rerank inference mean ≈2320ms
```

选择 multilingual MiniLM 为 production default：Recall 与 BGE 相同、0 regression，MRR 只少 `.026667`，inference ~9.2x faster。

代码/default env 已同步，server restart 后加载。

## DOCX semantic-gap curated eval

12-case curated DOCX regression：

English reranker：

```text
baseline Recall@5=.5000
flat manual expansion Recall@5=.8472
manual multi-query Recall@5=.9583
```

multilingual：

```text
baseline Recall@5=.8750
flat manual expansion Recall@5=.9444
manual multi-query Recall@5=1.0000
```

这些 manual expansions 是 retrieval architecture probes，不是 production automatic rewrite 证据。

## Auto decomposition probe — rejected

LLM auto query decomposition：

```text
Recall@5=.875
AnyHit@5=.9167
MRR@5=.7917
query generation mean≈1357.7ms
```

没有 coverage gain，且增加额外 LLM hop。

结论：不作为 mandatory production path。

## Grounded PRF probe — promising but not default

first-pass retrieval snippets → LLM grounded retrieval subqueries：

```text
Recall@5=.9444
AnyHit@5=1.0000
MRR@5=.9583
query-generation mean≈2137.6ms
end-to-end mean≈5415.4ms
```

质量改善但额外 LLM latency 显著；exact permission query 仍只恢复部分 gold，因此未直接上线。

## Structural rerank expansion — rejected as default

same-parent section expansion + rerank：

```text
baseline Recall@5=.8750 / AnyHit=.9167
structural Recall@5=.8750 / AnyHit=.9167
CandidateRecall .9167 → .9722
Recall@20 .9167 → .9444
```

Candidate coverage 有提升，但 Top5/AnyHit/MRR practical gain 不足；不作为 unconditional production expansion。

---

# Verifier-Driven Second-Chance Recovery

## 第一版 production recovery

只在：

```text
Verifier #1 = INSUFFICIENT
```

时触发；`CONFLICTING` 立即拒答。

流程：

```text
first top5
→ insufficient
→ top20 recovery anchors
→ parent section sibling expansion
→ second verifier
```

避免 mandatory LLM query rewrite。

## Trace 发现 recovery budget allocation bug

真实 trace：

first-pass top5 被 project overview 等泛化 chunk 占据；MCP section anchors 在 top20 rank6/16/20。

第一版 parent groups：

```text
项目总览 support=8 selected
MCP Gateway support=3 selected
```

12 additions 中前8个继续扩“项目总览”，MCP 只获得4个，真正 Q38/Q40/Q44 未完整进入 verifier。

### Fix

Recovery parent selection 改为：

```text
prefer novel parent sections not already covered by first-pass
```

并排除 top20 anchors，不让已有 anchor 重复消耗 addition budget。

新 trace：

```text
group01 selected = MCP Gateway
group02 selected = 系统设计
项目总览 = FIRST_PASS_COVERED
```

additions 成功包含：

```text
chunk39 Q38
chunk41 Q40
```

## 第二个真实结构问题：Q44 跨 section

数据库确认：

```text
chunk44 Q43 -> 五、MCP Gateway v2
chunk45 Q44 -> 六、HITL / Reliability / Checkpoint
```

所以 Q44 没进入 same-parent sibling 不是 parser bug，而是业务机制真实跨章节。

### Boundary bridge

新增 bounded boundary-aware policy：

- same-parent siblings；
- selected anchor 的 `chunk_index ±1` 邻接可以跨 section；
- 只允许局部桥接；
- chunk 保留真实 section/provenance；
- nearest structural evidence 优先排序。

不 hard-code EnterpriseOps / Q44 / chunk45。

## Final live E2E

最终 exact query：

```text
EnterpriseOps项目中权限校验是如何实现的？
```

response：

```text
refused=false
citations=4
```

Evidence：

- Q44 HITL：WRITE/DANGEROUS approval path，批准前不执行；
- Q38：READ/WRITE/DANGEROUS + unknown default dangerous；
- Q39：dynamic Tool Discovery metadata + permission class；
- Q40：server call 前 schema validation。

Citation 对 Q44 保持真实 section：

```text
六、HITL / Reliability / Checkpoint
```

结论：DOCX semantic-gap production live E2E **PASS**。

准确的权限架构表述：Gateway 负责 discovery / schema / permission class / approval metadata；真正 WRITE/DANGEROUS hard control 在 execution boundary，不能简化成“全部权限由 Gateway 自己 enforce”。

---

# P6-5 Failure Recovery / Partial Failure

## 目标

验证中途失败后：

- idempotency 不永久卡 `PROCESSING`；
- conversation 不留 half turn；
- stale CAS output 不落库；
- indexing partial failure 不把 FAILED data 变成 searchable evidence；
- retry 有边界。

## P6-5A Provider failure

新增/覆盖 provider timeout 与 bounded retry exhaustion。

结论：transient retry 是有限的；exhausted 后 structured failure，不无限放大。

## P6-5B Idempotency failure -> retry

API/regression + live PostgreSQL probe：

```text
PROCESSING
→ FAILED
→ same key/same hash
→ PROCESSING
→ COMPLETED
```

live：

```text
P6-5B idempotency FAILED -> retry -> COMPLETED: PASS
```

## P6-5C Conversation + failure composition

覆盖：

- provider failure 不写 user/assistant half exchange；
- CAS conflict 后 stale generated answer 不写 conversation；
- associated idempotency attempt 不永久停在 PROCESSING。

focused regression：PASS。

## P6-5D Ingestion partial failure

发现一个实际 fail-closed gap：

- IndexingService 已有 partial Qdrant compensation delete；
- 但如果 compensation delete 本身也失败，orphan vector 可能残留；
- Answer-side authoritative `ChunkRepository` 原先没有像 BM25 一样显式过滤 document status。

### Fix

`ChunkRepository` / recovery authoritative load 与 BM25 searchable contract 对齐：

```text
workspace match
AND deleted_at IS NULL
AND status IN (COMPLETED, PARTIAL)
```

因此：

```text
Qdrant orphan hit
→ PG chunk/document authoritative load
→ FAILED
→ reject
```

### Live DB probe

真实 PostgreSQL：

1. 创建 temporary probe document；
2. Document/Chunk commit；
3. simulated post-commit indexing failure；
4. Document → FAILED；
5. persisted chunks 确认存在；
6. Answer ChunkRepository 返回 empty；
7. BM25 corpus 不包含 probe chunks；
8. cleanup temporary data。

结果：

```text
P6-5D committed chunks + indexing failure -> FAILED + non-searchable: PASS
P6 failure recovery live DB probe: PASS
```

Targeted tests + full `pytest -q` + `git diff --check`：用户本地确认全部 PASS。

P6-5：**PASS**。

---

# P6 Final Gate

## 已验证

```text
Auth/token validation                       PASS
Workspace server-side authorization         PASS
Cross-user list/answer/upload isolation     PASS
Conversation optimistic CAS                 PASS
Idempotency replay/conflict/failure retry   PASS
Short transaction boundary                  PASS
DOCX parsing/routing/chunking/indexing       PASS
Multilingual reranker production choice     PASS
Semantic-gap bounded recovery               PASS
Cross-section boundary bridge               PASS
Citation provenance                         PASS
Provider failure recovery                   PASS
Ingestion partial-failure searchable gate   PASS
Full regression                             PASS
```

P6 final：

> **PASS WITH EXPLICIT KNOWN LIMITATIONS**

## Known limitations

- ingestion PG commit / idempotency completion narrow crash window；
- 不声称 exactly-once / distributed transaction；
- Qdrant cleanup 仍可能 best-effort，correctness 由 PG authoritative gate fail-closed；
- P6 concurrency 是 CAS竞争验证，不是高并发压力测试；
- open registration / rate limit / lockout / explicit CSRF 等 auth hardening backlog；
- DOCX image/multimodal understanding 未实现；
- destructive authorization 对真实 doc530 未留单独 live attack evidence；
- semantic recovery 通过真实 case，但不外推成所有跨章节问题 100% 解决。

## Next

P7：high-concurrency / load / service-level production validation：

```text
throughput
P50/P95/P99
DB pool saturation
embedding/reranker contention
provider concurrency
retry amplification
backpressure
rate limiting
timeout budget
cancellation/cleanup
graceful degradation
```

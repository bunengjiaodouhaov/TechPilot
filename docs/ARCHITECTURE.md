# TechPilot ARCHITECTURE

> 本文描述 **P6 结束后的当前生产架构**。历史阶段决策与实验过程见 `DEV_LOG.md`；当前 Gate 与 known limitations 见 `PROJECT_STATUS.md`。Day17 的 Dense-only production freeze 属于历史状态，已被 P6 production composition supersede。

## 1. 核心原则

TechPilot 当前仍遵循几条不会交给 Prompt 或模型自行保证的硬边界：

```text
PostgreSQL = authoritative business/evidence source
Qdrant     = rebuildable dense index
Retriever  = candidate discovery / ranking
Verifier   = evidence sufficiency gate
LLM        = semantic generation / bounded reasoning
Harness    = schema / permission / timeout / failure / trace boundary
```

关键原则：

- `candidate != evidence`；
- Retrieval relevance 不等于 Evidence sufficiency；
- request body 中出现 `workspace_id` 不等于调用者有权访问；
- Prompt 是软约束，不是 security boundary；
- 引用由服务端绑定真实 evidence provenance，不由模型自由生成；
- 异步慢调用期间不长期持有数据库事务；
- retry 必须有 ownership / budget，不做无界重试；
- WRITE / DANGEROUS 不能因 timeout 被盲目重放；
- FAILED / deleted Document 即使存在残留 index，也不能跨过 PostgreSQL authoritative gate。

## 2. Identity / Authentication / Authorization

### 2.1 Authentication

```text
Client
→ Authorization: Bearer <JWT>
   or HttpOnly techpilot_access_token cookie
→ decode_access_token
→ active user lookup
→ AuthPrincipal(user_id, email, is_demo)
```

当前认证组件：

- PBKDF2-SHA256 password hashing；
- JWT HS256；
- `/auth/register`；
- `/auth/token`；
- `/auth/logout`；
- `/auth/me`；
- production 模式禁止使用默认开发 secret。

Authentication 回答“你是谁”，不回答“你能访问哪个 workspace”。

### 2.2 Authorization

```text
AuthPrincipal
→ WorkspaceAuthorizer.require_access(...)
→ workspace_member(user_id, workspace_id, role)
→ operation allowed
```

原则：

- Workspace create 与 OWNER membership 在同一业务事务中建立；
- Workspace list 只返回当前 principal 可访问的 workspace；
- Answer / Upload / Conversation / Product Memory 等 workspace-scoped endpoint 重新执行 server-side authorization；
- 不因为调用方知道某个 `workspace_id` 就授予访问；
- unauthorized resource 主要返回 `404`，降低存在性泄露；
- repository/cache manifest 也绑定 owner identity，避免共享缓存跨用户复用。

## 3. 文档摄取架构

当前支持：

```text
Markdown
PDF
DOCX
```

链路：

```text
POST /documents/upload
→ authentication
→ workspace authorization
→ idempotency begin (when key provided)
→ ParserRouter
   ├─ MarkdownParser
   ├─ PDFParser / OCR boundary
   └─ DOCXParser
→ StructureAwareChunker
→ Document + Chunk
→ PostgreSQL COMMIT
→ IndexingService
   ├─ EmbeddingProvider
   └─ QdrantRepository
→ Document COMPLETED / PARTIAL
→ endpoint idempotency completion
```

### 3.1 PostgreSQL-first

Document / Chunk 先提交 PostgreSQL，再构建 Qdrant index。

这样避免：

```text
Qdrant success
+ PostgreSQL rollback
→ vector without authoritative record
```

代价是存在另一方向的 partial failure：

```text
PostgreSQL chunks committed
→ Qdrant indexing fails
→ Document FAILED
```

因此 production correctness 不能依赖“Qdrant cleanup 永远成功”，必须由 PostgreSQL searchable-state gate兜底。

### 3.2 DOCX parser

DOCX 通过 `python-docx` 解析正文，并按 document body 顺序处理 paragraph / table；Heading 1–6 形成 section path。

Title resolution：

```text
Title style
→ Heading 1
→ DOCX core title
→ filename stem
```

Chunk metadata 保留：

- `document_id`
- `chunk_index`
- `section`
- source type
- paragraph/table-related metadata

当前 DOCX 不做：

- 图片语义理解；
- header/footer 正文摄取；
- nested table 的完整 layout semantics；
- legacy `.doc` / `.rtf`。

### 3.3 OOXML preflight

DOCX 在 parser 前进行 ZIP package guard：

- maximum entry count；
- maximum total uncompressed bytes；
- maximum per-entry bytes；
- compression ratio guard；
- required OOXML files；
- invalid ZIP / encrypted package rejection。

该层解决 package-level resource abuse，不等于 malware scanner。

## 4. Searchable Document Boundary

所有 authoritative answer-side chunk load 与 BM25 语料统一要求：

```text
Document.workspace_id == request.workspace_id
Document.deleted_at IS NULL
Document.status IN (COMPLETED, PARTIAL)
```

这条规则非常重要。

即使 Qdrant 因 compensation cleanup failure 留下了 orphan vector：

```text
Qdrant hit
→ point_id
→ PostgreSQL ChunkRepository
→ Document.status == FAILED
→ reject / no authoritative chunk
```

所以 Qdrant residual state 不能直接变成 Answer evidence。

## 5. Current Production Retrieval

P6 当前 `/answers` production path：

```text
                    ┌─ DenseRetrievalService
User Query ─────────┤
                    └─ BM25RetrievalService
                               ↓
                         RRF Hybrid
                               ↓
                   CrossEncoder Reranker
                               ↓
                  AnswerRetrievalAdapter
                               ↓
                         top evidence
```

### 5.1 Dense

- model：`intfloat/multilingual-e5-base`；
- query 使用 `query:`；
- passage 使用 `passage:`；
- Qdrant 强制 workspace filter；
- Qdrant 只保存 retrieval payload，不作为完整正文事实来源。

### 5.2 BM25

- 中英混合 tokenizer；
- 技术 token / identifier 优先保护；
- 中文片段使用 jieba；
- `k1=1.5`；
- `b=0.75`；
- legal corpus 来自 PostgreSQL searchable boundary。

### 5.3 RRF

不直接把 Dense similarity 和 BM25 score 相加。

```text
RRF(d) = Σ 1 / (k + rank_i(d))
```

当前配置核心边界：

```text
candidate_limit = 40   # production config boundary, each adapter uses bounded candidates
rerank_depth    = 20
final answer top-k is separately bounded
rrf_k           = 60
```

注意：历史 P2 strict eval 使用 `candidate_limit=20 / rerank_depth=20 / top_k=5`；production configuration 与 benchmark configuration 要分开描述，不能把不同 run 的参数混成同一结论。

### 5.4 Production reranker model decision

P6 之前代码默认曾使用 English-oriented：

```text
cross-encoder/ms-marco-MiniLM-L-6-v2
```

严格 30-case P2 Golden 在同环境下：

```text
old ms-marco
Recall@5 = 0.666667
MRR@5    = 0.578889
regressions = 5
rerank inference mean ≈ 147 ms
```

multilingual candidate：

```text
cross-encoder/mmarco-mMiniLMv2-L12-H384-v1
Recall@5 = 0.866667
MRR@5    = 0.740000
regressions = 0
rerank inference mean ≈ 253 ms
```

BGE baseline：

```text
BAAI/bge-reranker-v2-m3
Recall@5 = 0.866667
MRR@5    = 0.766667
regressions = 0
rerank inference mean ≈ 2320 ms
```

最终 production default：

```text
cross-encoder/mmarco-mMiniLMv2-L12-H384-v1
```

原因不是“它离线最高分”，而是 quality / latency trade-off：

- 与 BGE Recall 相同；
- 都是 0 regression；
- MRR 只少约 `0.0267`；
- rerank inference 约快 `9.2x`。

## 6. Trusted Answer Pipeline

普通一次回答：

```text
POST /answers
→ authentication
→ workspace authorization
→ optional idempotency begin
→ optional conversation snapshot/version
→ commit / release request DB transaction
→ retrieval
→ authoritative chunk materialization
→ Context Builder
→ Evidence Verifier #1
```

Verifier state：

```text
SUFFICIENT
INSUFFICIENT
CONFLICTING
```

### 6.1 Sufficient

```text
SUFFICIENT
→ keep only verified supporting sources
→ Answer Provider
→ validate internal SOURCE_N selection
→ server-built Citation
```

Citation metadata来自 authoritative evidence：

- document name；
- page range when applicable；
- section；
- quote。

模型不能自由指定任意文件名或 quote。

### 6.2 Conflicting

```text
CONFLICTING
→ refuse
```

Conflict 不进入 semantic recovery。否则 recovery 有可能通过扩大证据池“洗掉”真实冲突。

### 6.3 Insufficient → bounded second chance

P6 新增 verifier-driven recovery：

```text
Verifier #1 = INSUFFICIENT
→ retrieve bounded recovery anchors
→ inspect authoritative structure
→ choose recovery parent groups
→ load bounded structural additions
→ Context Builder
→ Evidence Verifier #2
   ├─ SUFFICIENT → generate
   └─ otherwise  → refuse
```

Recovery 不是 mandatory query rewrite；只有第一次 verifier 判定 `INSUFFICIENT` 时才触发。

## 7. Structural Recovery Policy

DOCX semantic-gap case 暴露了两个结构问题。

### 7.1 Novel parent-section preference

原始 implementation 先按 parent group support count 排序，导致 first-pass 已经大量覆盖的“项目总览”继续吞掉 recovery budget。

修正后：

```text
first-pass parents = already-covered regions
recovery candidate groups
→ prefer parent groups not covered in first pass
→ then rank by support / best rank
```

Recovery 的目标是补新证据区域，不是重复 first-pass。

同时 additions 排除已有 recovery anchors，避免把 top-N anchor 再次作为“新增 evidence”消耗预算。

### 7.2 Boundary-aware ±1 bridge

真实 EnterpriseOps evidence：

```text
Q38 / Q40 → 五、MCP Gateway v2
Q44       → 六、HITL / Reliability / Checkpoint
```

权限实现跨父章节：Gateway 负责 risk/schema/metadata，HITL 在工具执行边界做 approval hard control。

因此只做 same-parent sibling expansion 仍会漏 Q44。

当前 policy：

- selected parent 的真实 sibling；
- selected anchors 的紧邻 `chunk_index ±1` 可以作为 bounded boundary bridge；
- 跨 section chunk 保留自己的真实 section metadata；
- 不允许任意跨 section expansion。

示意：

```text
Q43 chunk44  [selected MCP anchor]
      ↓ +1
Q44 chunk45  [HITL section, boundary bridge]
```

这不是把 Q44 伪装成 MCP parent，而是用局部结构邻接发现跨章节机制。

## 8. Conversation Concurrency

Conversation 不把 lock/transaction 跨 retrieval + LLM 保持数秒。

```text
read conversation
read current version V
read recent turns
→ COMMIT
→ slow answer generation
→ UPDATE/append WHERE version = V
```

若期间已有另一请求成功写入：

```text
expected_version != current_version
→ ConversationConflictError
→ HTTP 409
```

失败请求不能留下 stale user turn / assistant half-turn。

当前实现是 optimistic CAS，不是 distributed queue / per-conversation mutex。

## 9. Idempotency Architecture

`Idempotency-Key` 不是普通 cache key。

scope：

```text
user_id
workspace_id
operation
key
request_hash
```

状态：

```text
PROCESSING
COMPLETED
FAILED
```

规则：

```text
new key
→ PROCESSING

COMPLETED + same request hash
→ replay stored status / JSON

same key + different request hash
→ conflict

PROCESSING duplicate
→ conflict

FAILED + same request hash
→ reset to PROCESSING and retry
```

Request hash 对 typed request JSON 做稳定 canonical serialization 后 SHA-256。

不声称 exactly-once：

- application idempotency record 与外部系统副作用不是 distributed transaction；
- ingestion 中 PostgreSQL Document/Chunk commit 可以早于 endpoint idempotency completion；
- crash 可能发生在两者之间。

## 10. Transaction Boundary

核心原则：

> Database transaction protects database mutation; it should not be used as a request-lifetime mutex around LLM/network calls.

`/answers` 在进行 verifier / generator 等慢调用前释放当前数据库事务，然后最后再通过 CAS / idempotency 状态完成必要写入。

价值：

- 降低 connection pool 占用；
- 降低长事务；
- 避免 LLM latency 直接变成 DB lock duration；
- 将 stale write 明确转换成 version conflict。

## 11. Failure Recovery

### 11.1 Provider retry

Provider / retry helper 对 transient category 做 bounded retry；非 retryable error fail-fast。

必须区分：

```text
provider classifies failure
retry policy owns attempt count / delay / exhaustion
```

测试覆盖 transient timeout 最终 exhaustion；不存在无限 retry。

### 11.2 Answer failure

若 answer provider / verifier 最终失败：

- 请求失败；
- idempotency record 标记 `FAILED`；
- conversation 不追加 half-turn；
- same key + same request 可以重新执行。

### 11.3 CAS conflict composition

即使模型已生成 answer，如果 conversation version 已变化，stale output 不能写入 conversation。

CAS conflict 同样需要正确结束 idempotency attempt，避免永远停在 `PROCESSING`。

### 11.4 Ingestion partial failure

```text
Document + Chunk committed
→ indexing failure
→ Qdrant document-point compensation
→ Document FAILED
```

若 compensation delete 自身也失败：

```text
orphan Qdrant points may remain
BUT
PostgreSQL authoritative load rejects FAILED document
BM25 corpus rejects FAILED document
```

这是当前 fail-closed safety net。

长期如果需要 operational cleanup guarantee，可加入 outbox / reconciliation job；当前 P6 不把 best-effort cleanup 夸大成 exactly-once index consistency。

## 12. Repository / Agent Harness Boundary

P3/P4 的核心原则保持不变：

```text
Thin semantic control
→ Thick deterministic Harness
```

Harness 负责：

- Tool schema validation；
- permission class；
- timeout；
- structured failure；
- repository read boundary；
- evidence materialization；
- trace / evaluation。

Agent/LLM 可以决定下一步想调用什么，但不能自行扩大 filesystem / permission scope。

Repository search / AST / call clue 都是 candidate discovery。最终 CodeEvidence 仍必须由 authoritative `read_file` 构建。

## 13. Tool Permission Boundary

项目中工具风险分类使用：

```text
READ
COMPUTE
WRITE
DESTRUCTIVE / DANGEROUS (context-dependent naming)
```

对于 MCP Gateway 业务描述，文档使用 `READ / WRITE / DANGEROUS`。

关键安全语义：

- unknown mutating capability 不默认 READ；
- schema validation 在真正 server call 前发生；
- READ transient failure 可以有限 retry；
- WRITE timeout 不自动盲重试，因为无法知道 server 是否已产生副作用；
- WRITE/DANGEROUS approval 是工具执行边界的硬控制，不是仅在 Prompt 中要求“先问用户”。

## 14. Evaluation Boundary

不同指标回答不同问题：

```text
Retrieval metrics
→ candidate quality

Evidence Verifier eval
→ sufficiency / safety gate

Answer audit
→ generated correctness + citation support

Agent eval
→ task / source / decision-context / requirement / control behavior

Production validation
→ auth / isolation / concurrency / idempotency / partial failure semantics
```

不能用“pytest 100%”替代真实业务/live validation，也不能用单次 live response 代替大样本质量结论。

## 15. P6 Live Evidence

已完成的关键真实验证：

- cross-user workspace list/answer/upload isolation；
- same-conversation concurrent `/answers` CAS conflict；
- idempotency exact replay；
- same key different payload conflict；
- multilingual reranker strict 30-case comparison；
- real DOCX ingest/index/retrieve；
- exact EnterpriseOps permission semantic-gap query：`refused=false` + grounded citations；
- P6 failure-recovery PostgreSQL live probe；
- full regression PASS。

## 16. Current Known Limits

- P6 concurrency evidence 不是高并发 load test；
- ingestion commit / idempotency completion 存在 narrow crash window；
- Qdrant orphan cleanup 仍是 best-effort + authoritative fail-closed，不是 transactional consistency；
- registration/rate-limit/lockout/CSRF 等 auth product hardening 尚有 backlog；
- DOCX 图片尚未 multimodal parse；
- complex PDF/DOCX layout 仍有结构恢复上限；
- Research Agent 的 multi-obligation / goal-drift limitation 仍存在；
- 未逐条 live attack 所有 destructive endpoint；
- service-level P95/P99、pool saturation、backpressure、provider concurrency 尚未做 P7 压测。

## 17. Next Architecture Validation: P7

P7 不再证明“单请求功能能否工作”，而是验证系统在并发下的资源和失败传播：

```text
N concurrent users
→ FastAPI request lifetime
→ auth / DB pool
→ BM25 corpus work
→ Dense embedding
→ Qdrant
→ CrossEncoder
→ verifier / generator provider
→ retries
→ response / timeout
```

重点观测：

- throughput；
- P50 / P95 / P99；
- event-loop blocking；
- DB pool saturation；
- reranker serialization / CPU-MPS contention；
- provider concurrency；
- retry amplification；
- queue/backpressure；
- rate limiting；
- graceful degradation；
- cancellation / cleanup。

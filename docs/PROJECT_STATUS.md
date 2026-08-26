# TechPilot PROJECT_STATUS

## 当前版本

v0.7-dev

## 当前阶段

**P6 Production Boundary Hardening / Production Validation：PASS WITH EXPLICIT KNOWN LIMITATIONS。**

P5 Job Intelligence 已冻结为真实业务 prototype，不是 Product Gate PASS。P6 已完成认证授权、workspace 隔离、并发与幂等、DOCX 生产链路、检索 reranker 选型、semantic-gap recovery 以及 failure-recovery 的真实验证。下一阶段不继续围绕单个 RAG case 调参，转向 **P7 高并发 / 压力 / 服务级性能与可靠性验证**。

## 阶段状态

- P0 工程骨架：**PASS**
- P1 文档 RAG：工程闭环完成；历史质量问题已在 P2 / Evaluation Backfill 中继续处理
- P2 高质量 RAG：**PASS**；400-case retrieval / 180-case Answer-Evidence backfill 已完成
- P3 Code RAG：**FINAL PASS WITH KNOWN LIMITATIONS**
- P4 Research Agent：**PASS WITH KNOWN LIMITATIONS**
- Evaluation Backfill：**COMPLETE**
- P5 Job Intelligence：**CLOSED AS A BUSINESS PROTOTYPE — NOT A PRODUCT GATE PASS**
- P6 Production Boundary Hardening：**PASS WITH EXPLICIT KNOWN LIMITATIONS**
- 下一步：P7 service-level load / high-concurrency / latency / backpressure / rate-limit validation

## P6 当前生产能力

### 1. Authentication / Authorization / Workspace Isolation

当前 API 不再信任 request body 中的 `workspace_id` 作为授权依据。

```text
Bearer / HttpOnly Cookie
→ JWT decode
→ active User
→ AuthPrincipal(user_id)
→ WorkspaceAuthorizer
→ workspace_member
→ allowed operation
```

已实现：

- `/auth/register`、`/auth/token`、`/auth/logout`、`/auth/me`；
- PBKDF2-SHA256 password hashing；
- JWT HS256 access token；
- Bearer / `techpilot_access_token` HttpOnly cookie；
- production 环境拒绝默认开发 secret；
- Workspace create 时写入 OWNER membership；
- Workspace list / Answer / Upload / Conversation / Product Memory 等 workspace-scoped 路径统一授权；
- unauthorized workspace 默认以 `404` fail-closed，避免资源存在性泄露；
- repository manifest / cache 额外绑定 owner user identity，避免共享缓存跨用户复用。

真实 cross-user validation：

- User B owns workspace `179`；
- User A 对 workspace `179` 调用 `/answers`：`404 workspace not found`；
- User A 对 workspace `179` 上传：`404 workspace not found`；
- User A `GET /workspaces` 不可见 workspace `179`。

限制：针对 workspace `179` 的真实 document `530`，未单独记录一次 unauthorized destructive delete live evidence；因此不能把“所有 destructive resource 路径均已逐条 live 验证”作为结论。授权代码路径已覆盖，但这个具体 destructive live case 仍属于证据缺口。

### 2. Conversation Concurrency / CAS

Conversation 写入使用 version compare-and-set，而不是把数据库事务跨 LLM 慢调用保持打开。

```text
load conversation + version + recent history
→ commit / release DB transaction
→ retrieval / verifier / LLM
→ append_exchange_if_version(expected_version)
```

真实并发验证：

- 同一个 conversation 同时发出两条 `/answers`；
- 一个请求成功；
- 另一个返回 `409`：`conversation changed while the answer was generating; reload the latest history and retry`；
- Conversation 最终只存在成功请求的 user + assistant 两个 turn；
- stale 请求没有留下 half-turn。

P6-3：**LIVE PASS**。

### 3. Idempotency

支持 `Idempotency-Key`，scope 至少包含 user / workspace / operation / key，并绑定 request hash。

状态机：

```text
PROCESSING
→ COMPLETED
→ exact replay returns stored response

PROCESSING
→ FAILED
→ same request + same key may retry

same key + different request hash
→ 409 conflict
```

真实验证：

- 同 key + 相同 request：返回完全相同 JSON；replay 约 `10ms`；
- 同 key + 不同 request：`409 Idempotency-Key was already used for a different request`；
- P6-5 live DB probe 验证 `FAILED → same-key retry → COMPLETED`。

不允许描述为 exactly-once。Document/Chunk 的 PostgreSQL commit 与 endpoint idempotency completion 之间仍存在窄 crash window。

P6-4：**LIVE PASS WITH KNOWN CRASH WINDOW**。

### 4. DOCX Ingestion

当前支持：

- `.pdf`
- `.md` / `.markdown`
- `.docx`

DOCX parser 使用 `python-docx`，按 body 顺序处理 paragraph / table，并维护 Heading 1–6 路径；Chunk metadata 保留 section hierarchy。

OOXML ZIP preflight 包含：

- entry count limit；
- total uncompressed size limit；
- single-entry size limit；
- compression ratio guard；
- `[Content_Types].xml` / `word/document.xml` presence；
- bad ZIP / encrypted package rejection。

当前 DOCX 限制：

- 文档中的图片暂不做视觉理解；
- headers / footers 未作为正文解析；
- nested table 结构不做完整语义恢复；
- `.doc` / `.rtf` 不支持。

### 5. Current Production Retrieval

Day17 的 Dense-only production freeze 是历史状态，已被当前 P6 production composition supersede。

当前 `/answers` production retrieval：

```text
Dense multilingual-e5-base
      +
BM25
      ↓
RRF Hybrid
      ↓
CrossEncoder reranker
      ↓
AnswerRetrievalAdapter
```

当前 production reranker：

```text
cross-encoder/mmarco-mMiniLMv2-L12-H384-v1
```

严格 30-case P2 Golden，同一环境对比：

| Reranker | Recall@5 | MRR@5 | Regressions | Rerank inference mean |
| --- | ---: | ---: | ---: | ---: |
| old `ms-marco-MiniLM-L-6-v2` | 0.666667 | 0.578889 | 5 | ~147 ms |
| `mmarco-mMiniLMv2-L12-H384-v1` | 0.866667 | 0.740000 | 0 | ~253 ms |
| `BAAI/bge-reranker-v2-m3` | 0.866667 | 0.766667 | 0 | ~2320 ms |

生产选择 multilingual MiniLM，而不是 BGE：两者 Recall 相同、均 0 regression；BGE MRR 只高约 `0.0267`，但 rerank inference 约慢 `9.2x`。

### 6. DOCX Semantic-Gap Recovery

真实问题：

```text
EnterpriseOps项目中权限校验是如何实现的？
```

单轮 Hybrid + multilingual reranker 最初仍安全拒答。诊断确认：

- Parser / PostgreSQL persistence / Qdrant indexing 均正常；
- 关键证据分散在多个 chunk：
  - Q38：READ / WRITE / DANGEROUS；
  - Q40：Tool Schema validation；
  - Q44：HITL / approval hard boundary；
- Q44 位于下一父 section，单纯 parent-section expansion 不能覆盖完整复合机制。

最终 production recovery：

```text
first retrieval top-k
→ first Evidence Verifier
   ├─ SUFFICIENT  → generate
   ├─ CONFLICTING → refuse immediately
   └─ INSUFFICIENT
        ↓
      bounded second chance
        ├─ recovery top-N anchors
        ├─ prefer parent sections not already covered by first pass
        ├─ exclude existing anchors from additions
        └─ boundary-aware ±1 adjacent chunk bridge
        ↓
      second Evidence Verifier
        ├─ SUFFICIENT → generate
        └─ otherwise  → safe refusal
```

重要约束：

- `CONFLICTING` 不进入 recovery；
- 不为了这一个 query 硬编码 EnterpriseOps / Q44 / chunk id；
- boundary bridge 保留 chunk 真实 `section` provenance，不伪造父章节；
- recovery 是 verifier-driven second chance，不是每个请求固定增加一次 LLM rewrite。

最终真实 `/answers`：

- `refused=false`；
- citations 非空；
- 引用了 Q44 HITL、Q38 permission class、Q39 dynamic discovery、Q40 schema validation；
- citation section 保留真实 DOCX provenance。

DOCX semantic-gap live E2E：**PASS**。

### 7. Failure Recovery / Partial Failure

P6-5 覆盖：

- provider transient timeout / bounded retry exhaustion；
- `/answers` provider failure 后 idempotency 标记 `FAILED`；
- same-key retry 可以重新进入 `PROCESSING` 并完成；
- conversation provider failure 不写 half-turn；
- CAS conflict 后 stale answer 不落库；
- indexing partial failure 执行 Qdrant compensation delete；
- compensation cleanup 本身失败的路径有回归测试；
- PostgreSQL 已提交 chunks 但 document 最终 `FAILED` 时，Answer authoritative chunk load 与 BM25 都 fail-closed。

为此 `ChunkRepository` 的权威正文边界与 BM25 对齐，只允许：

```text
Document.deleted_at IS NULL
AND Document.status IN (COMPLETED, PARTIAL)
```

真实 PostgreSQL probe 验证：

```text
P6-5B idempotency FAILED -> retry -> COMPLETED: PASS
P6-5D committed chunks + indexing failure -> FAILED + non-searchable: PASS
P6 failure recovery live DB probe: PASS
```

P6-5：**PASS**。

## P6 Gate 结论

```text
Authentication / token validation          PASS
Workspace authorization / isolation        PASS
Conversation optimistic concurrency        PASS
Idempotency replay/conflict/retry           PASS
DOCX ingestion                             PASS
Production multilingual reranker           PASS
DOCX semantic-gap second-chance recovery   PASS
Citation provenance                        PASS
Provider failure recovery                  PASS
Ingestion partial-failure searchable gate  PASS
Full regression                            PASS
```

因此 P6 可以关闭为：

> **PASS WITH EXPLICIT KNOWN LIMITATIONS**

## 已知限制 / 不允许夸大

1. **不是 exactly-once。** Ingestion PostgreSQL commit 与 idempotency completion 之间存在窄 crash window。
2. **Qdrant 仍是可重建索引。** compensation cleanup failure 可留下 orphan vector；当前通过 PostgreSQL authoritative status gate 阻止其进入回答事实边界，长期仍可考虑 outbox/reconciliation。
3. **P6 的并发验证不是高并发压测。** 当前真实证据是同 conversation 的竞争请求 CAS；尚未给出 100/1000 并发、P95/P99、pool saturation、backpressure 结果。
4. **Auth 仍有产品级 hardening backlog。** open registration、rate limit / lockout、显式 CSRF 策略等尚未作为 P6 blocking gate 关闭。
5. **DOCX 不是完整多模态理解。** 图片、复杂嵌套布局等仍需后续 multimodal ingestion。
6. **Destructive authorization 的单个真实资源 case 证据不完整。** 不应说所有 destructive API 都逐条 live attack 验证过。
7. **Semantic recovery 是 bounded heuristic + verifier gate。** 当前真实复合问题已通过，但不能据此宣称所有跨章节语义问题均已解决。

## 下一步：P7 Service-Level Production Validation

下一阶段不继续对 EnterpriseOps 单题做优化，转向真实系统压力：

```text
concurrency levels
→ throughput / queueing
→ DB pool saturation
→ Qdrant / embedding / reranker bottleneck
→ provider concurrency / retry amplification
→ P50 / P95 / P99
→ timeout budget
→ backpressure / rate limiting
→ graceful degradation
→ resource cleanup
```

目标是回答：**系统在多个用户同时使用时会在哪里先坏、如何限制放大、如何恢复，而不是单请求能不能跑通。**

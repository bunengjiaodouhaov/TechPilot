# TechPilot INTERVIEW_NOTES

> 本文件保留项目历史面试口径，并在末尾增加 P6 当前生产状态。遇到历史段落与 P6 当前态冲突时，以末尾 **P6 Production Validation** 为准；例如 Day14/Day17 的“Reranker 不进生产 / Dense-only”是当时的真实决策，不是当前 production composition。

## 历史核心口径速查

### PostgreSQL 和 Qdrant 谁是事实来源？

PostgreSQL 是事实来源，保存 Document / Chunk 正文和业务状态；Qdrant 是可重建 Dense index。Retriever 命中的 Qdrant point 最终仍必须回查 PostgreSQL authoritative Chunk。

### 为什么 `candidate != evidence`？

Search / Retrieval 只说明“这里可能相关”。Evidence 还要满足 authoritative source、主体/属性/关系、provenance 和 context admission。Code RAG 也一样：search result / AST clue 只负责定位，最终要 `read_file -> CodeEvidence`。

### 为什么 Evidence Verifier 独立于 Retriever / Reranker？

Retriever / Reranker 优化 relevance；Verifier 判断 sufficiency / conflict。高相关并不能证明结论。`INSUFFICIENT / CONFLICTING` 应在 generator 前停止，而不是依赖生成模型自报 confidence。

### 为什么权限不能写在 Prompt 里就算了？

Prompt 是软约束。真正的 schema、permission、timeout、repository boundary、approval hard gate 必须在 deterministic Harness / ToolRuntime / execution boundary 强制执行。

### 为什么 Provider 不应该偷偷 retry？

Retry 必须有统一 ownership，否则 control layer 看不到真实 calls/cost/time，容易产生 retry amplification。Provider 负责 failure classification；retry policy / orchestrator 负责 bounded attempt、delay、exhaustion。

### P3/P4 一句话怎么讲？

> 我把 LLM semantic decision 和 deterministic Harness 分开：模型决定“下一步想做什么”，Harness 决定“能不能安全执行”，authoritative Evidence 决定“事实是什么”，Trace/Evaluation 决定“失败在哪一层”。

### Evaluation Backfill 数字怎么讲？

不要把所有数据包装成 clean human heldout。

Document Retrieval 400-case frozen engineering evaluation：

```text
Dense Recall@5 61.5% → Hybrid+CrossEncoder 83.4%
MRR@5          44.4% → 72.6%
nDCG@5         47.8% → 74.9%
Coverage       63.0% → 84.7%
P95 ≈ 867ms
```

Answer/Evidence 180-case：146 answered，assistant audit full correct `140/146 = 95.89%`；20-case source-binding adversarial set 上 Verifier v2 false-answer `5%`。

Code RAG：150-case structural/regression File Hit@5 `94.67%`、Content Hit `89.33%`、provenance `100%`；30-case realistic hard set File/Content Hit `93.33%`。

Research Agent 36-case backfill：negative correctness `100%`、false completion `0`、provenance `100%`；主要 limitation 是 multi-obligation decomposition / obligation persistence / semantic planning。

### P5 Job Intelligence 怎么讲？

准确口径：**CLOSED AS A BUSINESS PROTOTYPE — NOT A PRODUCT GATE PASS**。

牛客/实习僧存在真实 Flow A evidence；BOSS 稳定 full-JD discovery 未解决；Resume Flow B/C 未真实 E2E close。不能说已经 production-ready。

---

# P6 Production Validation — 高频详解

## Q1：P6 主要解决什么？

P6 不是继续做一个新的 Agent feature，而是把已有系统放到更接近 production 的边界下验证：

```text
authentication
authorization / tenant isolation
conversation concurrency
idempotency
transaction lifetime
DOCX real ingestion
production reranker choice
semantic-gap recovery
provider / ingestion partial failure recovery
```

核心问题从“功能能不能跑”变成：

> 多用户、并发、重复请求和中途失败时，系统状态是否仍然正确且 fail-closed？

## Q2：Authentication 和 Authorization 怎么区分？

Authentication：你是谁。

```text
Bearer / HttpOnly cookie
→ JWT decode
→ active User
→ AuthPrincipal
```

Authorization：你能访问什么。

```text
AuthPrincipal(user_id)
+ workspace_id
→ workspace_member
→ WorkspaceAuthorizer
```

不能因为 request body 带了 `workspace_id=179` 就认为有权限。

## Q3：为什么 unauthorized workspace 返回 404，而不是 403？

主要是减少资源存在性泄露。对于无权访问的调用者，系统不需要确认“这个 workspace 其实存在，只是你不能看”。

真实 cross-user validation 已证明 User A 无法通过 list / answer / upload 看到或访问 User B 的 workspace。

注意不要夸大：对真实 document 530 的 unauthorized destructive delete 没有单独留一条 live attack evidence，因此不要说“所有 destructive endpoint 都逐条真实攻击验证过”。

## Q4：密码和 token 做了什么？

当前：

- PBKDF2-SHA256；
- 310k iterations；
- random salt；
- constant-time verification；
- JWT HS256；
- Bearer / HttpOnly cookie；
- production 禁止默认 dev secret。

Known gaps：open registration、rate-limit/lockout、显式 CSRF hardening 等仍可继续做，不要把 P6 描述成完整 IAM 产品。

## Q5：Conversation 并发为什么不能直接开一个长事务锁住？

因为一次 `/answers` 可能包含 retrieval、reranker、Verifier、LLM，耗时可达到秒级。如果一直占着 transaction / connection：

- DB pool 更容易耗尽；
- lock 持有时间跟 LLM latency 绑定；
- provider timeout 会拖长 transaction；
- 高并发时形成放大。

所以使用 optimistic CAS：

```text
read conversation + version V
→ release DB transaction
→ slow answer work
→ append only if current version == V
```

真实两个并发请求：一个 200，一个 409；最终只写入成功请求的 user/assistant 两个 turn，stale 请求无 half-turn。

## Q6：为什么 CAS 冲突返回 409 是合理的？

它不是服务器随机失败，而是明确表示：

> 你生成 answer 使用的 conversation snapshot 已过期。

正确行为是 reload latest history 后重新生成，而不是把 stale answer 强行写进去。

## Q7：Idempotency-Key 怎么实现？

scope 不只是 key 本身：

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

相同 key + 相同 request：COMPLETED 后 replay stored response。

相同 key + 不同 request hash：409 conflict。

FAILED + 同 request：允许 reset 为 PROCESSING 后 retry。

## Q8：为什么这不叫 exactly-once？

因为 application idempotency record、PostgreSQL business commit、Qdrant index、外部 side effect 并不是一个 distributed transaction。

特别是 ingestion：

```text
Document/Chunk PostgreSQL commit
→ indexing / endpoint completion
```

中间存在 narrow crash window。

因此准确表述：

> 提供 request-level idempotent replay / duplicate protection，并明确保留 ingestion commit-completion crash window；不声称 exactly-once。

## Q9：P6 为什么把 DOCX 做进来了？

真实知识库/面试资料大量是 DOCX。只支持 PDF/Markdown 会使产品验证偏离真实输入。

当前 DOCX：

- `python-docx`；
- paragraph/table body order；
- Heading 1–6 stack；
- section provenance；
- OOXML ZIP preflight；
- structure-aware chunking。

不支持的要主动说：图片语义、header/footer、复杂 nested table、多模态 layout 还没完整处理。

## Q10：DOCX ZIP preflight 防什么？

主要防 package-level resource abuse / malformed OOXML：

- entry 数量；
- 解压总大小；
- 单 entry 大小；
- compression ratio；
- required OOXML files；
- bad/encrypted ZIP。

它不是通用 antivirus，也不要这样包装。

## Q11：为什么换 Reranker？

生产代码此前默认 `cross-encoder/ms-marco-MiniLM-L-6-v2`，它偏英文。严格 P2 30-case 中文/英文混合 Golden 上出现明显 regression：

```text
old ms-marco
Recall@5 0.666667
MRR@5    0.578889
regressions 5
```

multilingual：

```text
mmarco-mMiniLMv2-L12-H384-v1
Recall@5 0.866667
MRR@5    0.740000
regressions 0
```

因此 old English-oriented default 不适合当前 production corpus。

## Q12：为什么不选离线 MRR 更高的 BGE？

BGE：

```text
Recall@5 0.866667
MRR@5    0.766667
regressions 0
rerank inference mean ≈ 2320ms
```

multilingual MiniLM：

```text
Recall@5 0.866667
MRR@5    0.740000
regressions 0
rerank inference mean ≈ 253ms
```

两者 Recall 相同，MRR 只差约 `0.0267`，但 BGE inference 约慢 `9.2x`。

所以生产选择不是“最高 benchmark 分”，而是 Pareto trade-off。

## Q13：为什么换 multilingual reranker 后，EnterpriseOps 权限问题还是拒答？

因为 model choice 和 semantic-gap 是两个不同问题。

最初 exact query：

```text
EnterpriseOps项目中权限校验是如何实现的？
```

仍然：

```json
{"refused": true, "citations": []}
```

诊断发现 parser/persistence/indexing 都正确；关键 evidence 分散：

- Q38：READ / WRITE / DANGEROUS；
- Q40：Tool Schema validation；
- Q44：HITL / approval hard boundary。

单轮 top-k 没把复合机制拼完整，所以 Verifier 合理地拒答。

## Q14：为什么不直接把 top-k 调大？

“把 k 调到够大”会：

- 增加 context noise；
- 增加 reranker / verifier cost；
- 不能解释什么时候应该扩；
- 容易让所有请求承担极端 case 成本。

项目做过 structural ablation：candidate coverage / Recall@20 有提升，但 Top5 / AnyHit / MRR 没实质改善，所以没有把“无条件结构扩展 + rerank”直接上线。

## Q15：为什么自动 query decomposition 也没上线？

Eval-only auto decomposition 增加了额外 DeepSeek hop，质量基本接近 baseline，没有达到预设 gate，而且平均增加约 1.36s query-generation latency。

所以结论不是“有 LLM rewrite 就更聪明”，而是：

> 如果 rewrite 没带来可测量 coverage gain，就不应该让每个请求永久承担额外 LLM hop。

## Q16：最终 semantic-gap recovery 怎么做？

Verifier-driven second chance：

```text
first retrieval
→ Verifier #1
   ├─ SUFFICIENT  → answer
   ├─ CONFLICTING → refuse
   └─ INSUFFICIENT
        ↓
      bounded structural recovery
        ↓
      Verifier #2
        ├─ SUFFICIENT → answer
        └─ otherwise  → refuse
```

只有 `INSUFFICIENT` 触发 recovery；`CONFLICTING` 直接 fail-closed。

## Q17：Recovery 第一版出了什么 bug？

Parent group 选择先按 support count，导致 first-pass 已经大量覆盖的“项目总览”再次吃掉 recovery budget。

结果真正相关的 MCP Gateway section 虽然在 top20 中，却没有得到足够 sibling expansion。

修复：优先选择 first-pass 尚未覆盖的 novel parent sections；并排除 top20 anchors，不让已有 candidate 假装成“新增 evidence”。

这是一个典型的 budget-allocation bug，不是 embedding 模型 bug。

## Q18：为什么还需要跨 section boundary bridge？

因为权限实现本身跨章节：

```text
五、MCP Gateway
  Q38 permission class
  Q40 schema validation
  Q43 server failure

六、HITL / Reliability
  Q44 approval hard boundary
```

Q44 是下一章节，same-parent expansion 永远不会拿到它。

最终只允许 selected anchor 的 `chunk_index ±1` 作为 bounded boundary bridge，并保留它自己的真实 section provenance。

不是把 Q44 错标成 MCP section。

## Q19：最终真实答案证明了什么？

最终 `/answers`：

```text
refused=false
citations != []
```

引用真实覆盖：

- Q44：WRITE/DANGEROUS approval path、批准前不执行；
- Q38：READ/WRITE/DANGEROUS、unknown 默认 dangerous；
- Q39：dynamic discovery + permission class metadata；
- Q40：server call 前 schema validation。

这证明的是这个 production semantic-gap case 已闭环，不证明“所有跨章节问题 100% 解决”。

## Q20：权限机制面试时怎么准确描述？

不要说：

> 所有权限都由 MCP Gateway 自己 enforce。

更准确：

> Gateway 负责 tool discovery、schema validation、permission classification 和 approval metadata；真正对 WRITE/DANGEROUS 的 hard control 在工具执行边界，上游 approval 未通过前不产生 side effect。

Prompt 里的“先问我”只是软约束。

## Q21：P6-5 Failure Recovery 测了什么？

不是只测“接口报 500”。

验证：

- transient provider timeout 最终 bounded exhaustion；
- provider failure 后 idempotency → FAILED；
- same key 可以 retry → COMPLETED；
- conversation provider failure 不留 half-turn；
- CAS stale output 不落库；
- indexing failure 后 document → FAILED；
- PostgreSQL 已 commit 的 chunks 对 Answer / BM25 不可搜索。

## Q22：为什么 FAILED document 的 chunk 还可能存在？

因为 ingestion 采用 PostgreSQL-first：Document/Chunk 先 commit，然后再 index Qdrant。

如果 indexing 后失败：

```text
chunks exist in PG
Document.status = FAILED
```

这不等于 bug；关键是 searchable boundary 必须排除 FAILED。

## Q23：这次 P6-5 真修了什么 production bug？

之前 Answer-side `ChunkRepository` authoritative load 主要检查 workspace/deleted，并没有与 BM25 一样显式要求 `COMPLETED/PARTIAL`。

如果 Qdrant compensation cleanup 又失败，理论上 orphan vector 可能指向 FAILED document。

修复后：

```text
ChunkRepository authoritative materialization
→ only COMPLETED/PARTIAL
```

因此 orphan vector 最多是 index hygiene 问题，不能升级成 answer evidence。

## Q24：为什么说 fail-closed，而不是“Qdrant 完全一致”？

因为当前不是 transactional outbox / distributed transaction。

可能存在：

```text
FAILED document
+ orphan Qdrant points
```

但 Answer 事实边界会回 PostgreSQL 检查并拒绝。也就是说 correctness fail-closed，但 operational cleanup 仍可继续优化。

## Q25：P6 最值得讲的工程故事是什么？

可以讲 semantic-gap + failure boundary 两条。

第一条：

> 一个真实 DOCX 问题一直拒答。我没有先调 Prompt，而是用 trace 把 parser、BM25、Hybrid、reranker、candidate admission、parent expansion、Verifier 分层定位。最后发现两个真实 policy bug：recovery budget 被已覆盖 parent 吞掉，以及关键 HITL evidence 位于下一 section。最终通过 novel-parent priority + bounded boundary bridge，在保持真实 citation provenance 的情况下把 live E2E 跑通。

第二条：

> 做 partial-failure review 时发现，如果 Qdrant compensation 本身失败，FAILED document 可能残留 orphan vector。真正的修复不是承诺 cleanup 永不失败，而是把 authoritative Chunk load 也限制为 COMPLETED/PARTIAL，使 index residue 无法成为回答 Evidence。

这两条比“我用了某个 Reranker”更有工程含量。

## Q26：P6 Gate 到底是什么状态？

准确：

```text
PASS WITH EXPLICIT KNOWN LIMITATIONS
```

不要说“production-ready fully solved”。

已真实证明：auth isolation、CAS、idempotency、DOCX semantic-gap live E2E、failure-recovery live DB probe、full regression。

仍未证明：大规模高并发、P95/P99 saturation、完整 IAM hardening、DOCX multimodal、所有 destructive endpoint 的逐条 live attack。

## Q27：下一步为什么是高并发而不是再做 RAG 优化？

因为当前单请求正确性已经有足够证据。下一类真实 production risk 是资源竞争：

```text
DB pool
embedding
BM25 request work
Qdrant
CrossEncoder
LLM provider
retry amplification
```

需要测：

- throughput；
- P50/P95/P99；
- concurrency collapse point；
- backpressure；
- rate limit；
- timeout budget；
- provider / reranker contention；
- graceful degradation。

这才回答“多个真实用户一起用会怎样”。

## P6 面试表述红线

可以说：

- workspace-level server-side authorization；
- live cross-user isolation；
- optimistic conversation CAS；
- request idempotent replay / conflict / retry；
- DOCX production ingestion；
- multilingual reranker 根据质量/延迟 trade-off 选型；
- verifier-driven bounded recovery；
- indexing partial-failure fail-closed；
- full regression + live probes PASS。

不要说：

- exactly-once；
- distributed transaction；
- 所有 destructive API 都逐条 live attack 通过；
- 已完成 100/1000 并发压测；
- 所有 DOCX 多模态信息都能识别；
- BGE 不如 multilingual MiniLM；BGE 离线 MRR 实际略高，只是生产延迟代价过大；
- recovery 解决所有 semantic-gap；
- Job Intelligence P5 product gate PASS。

## P6 30 秒增量介绍

> P6 我主要做的是 production boundary hardening，而不是继续堆 Agent feature。我补了 JWT + workspace membership 的服务端授权、Conversation optimistic CAS、Idempotency 状态机和短事务边界；同时把真实 DOCX 接进摄取链路。检索侧用严格 30-case regression 比较了英文 MiniLM、多语言 MiniLM 和 BGE，最终生产选择多语言 MiniLM，因为和 BGE Recall 相同但 inference 大约快 9 倍。一个真实 EnterpriseOps 权限问题仍然因为跨章节 semantic gap 被拒答，我最后做了只在 Verifier insufficient 时触发的 bounded structural recovery，并通过 novel-section priority 和 ±1 boundary bridge 找到 HITL 证据，真实 `/answers` 最终返回 grounded citations。P6-5 还验证了 provider failure、idempotency retry 和 indexing partial failure，确保 FAILED 文档即使有残留向量也不能进入 Answer evidence。

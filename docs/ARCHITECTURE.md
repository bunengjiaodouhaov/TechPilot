# TechPilot ARCHITECTURE

> 本文只描述系统如何工作。项目进度见 `PROJECT_STATUS.md`，历史决策见 `DEV_LOG.md`，操作步骤见 `RUNBOOK.md`。

## 1. 核心边界

- PostgreSQL 是事实来源，保存 Workspace、Document、Chunk 及完整正文。
- Qdrant 是可重建的 Dense 向量索引，不保存完整 Chunk 正文。
- Retrieval 负责召回候选证据，不负责判断 Evidence Sufficiency。
- Citation 只能绑定真实进入 Context 的 Chunk。
- 无充分证据时返回 Refused。
- 所有检索必须先限定合法候选集：当前 `workspace_id`、未软删除文档；BM25 还只接受 COMPLETED/PARTIAL Document。
- Dense、BM25 与 Hybrid 共享同一 Chunk 身份空间。
- Hybrid 只融合 Retriever 已经召回的候选，不能恢复 Dense/BM25 都没有召回的 Chunk。

## 2. 文档摄取链路

```text
HTTP Upload
  ↓
FastAPI documents route
  ↓
IngestionService
  ↓
ParserRouter
  ↓
MarkdownParser / PDFParser
  ↓
StructureAwareChunker
  ↓
Document + Chunk ORM
  ↓
PostgreSQL Commit
  ↓
IndexingService
  ├── EmbeddingProvider
  └── VectorRepository
        ↓
      Qdrant
```

关键约束：

- Document / Chunk 先提交 PostgreSQL，再执行 Dense 向量索引。
- Qdrant Point ID 使用 PostgreSQL Chunk 主键。
- Qdrant Payload 只保存检索与引用所需元数据。
- Chunk 业务 ID 基于结构与正文生成稳定 SHA-256；corpus 内容或切块变化时 Golden 标签必须重新核对。

## 3. 检索链路

### 3.1 Dense Retrieval

```text
User Query
  ↓
DenseRetrievalService
  ↓
EmbeddingProvider(query: ...)
  ↓
VectorRepository
  ↓
Qdrant Top-K + workspace filter
  ↓
VectorSearchHit(point_id, score, payload)
```

当前模型：

- `intfloat/multilingual-e5-base`
- Query：`query:` 前缀
- Document：`passage:` 前缀
- 768 维归一化向量
- Cosine Distance

### 3.2 BM25 Retrieval

```text
User Query
  ↓
BM25RetrievalService
  ├── mixed Chinese / technical tokenizer
  └── BM25ChunkRepository
        ↓
      PostgreSQL legal Chunk corpus
        ↓
      BM25 score / sort / Top-K
```

BM25 合法语料边界：

- `Document.workspace_id == workspace_id`
- `Document.deleted_at IS NULL`
- `Document.status IN (COMPLETED, PARTIAL)`

Tokenizer：

- ASCII 技术 token 先整体识别并 lowercase，例如 `workspace_id`、`Recall@5`、`SHA-256`、`3.11`
- 连续中文片段交给 jieba
- 标点与空白不作为 token

当前 BM25 baseline 只对 `chunk.text` 评分，避免把 Day 11 诊断脚本中的 metadata-enriched lexical design 偷渡为生产基线。

### 3.3 RRF Hybrid Retrieval

```text
                    ┌─ DenseRetrievalService ── Top candidate_limit
User Query ─────────┤
                    └─ BM25RetrievalService ─── Top candidate_limit
                                   ↓
                          reciprocal_rank_fusion
                                   ↓
                              Hybrid Top limit
```

RRF：

```text
RRF(d) = Σ 1 / (k + rank_i(d))
```

关键边界：

- 不直接相加 Dense 与 BM25 原始 score；两者 score scale 和语义不同。
- RRF 只依赖各 Retriever 内部 rank。
- 跨 Retriever 使用稳定 `chunk_id` 去重和聚合。
- 同一路重复 Chunk 不允许重复贡献。
- `candidate_limit` 是每一路进入 fusion 的候选深度。
- Dense/BM25 各自 Top-N 的 union 最多可包含 `2 * candidate_limit` 个不同 Chunk。
- `limit` 是 Fusion 后返回深度，因此可大于单路 `candidate_limit`，但不得超过理论 union 上限。
- `HybridSearchHit` 保留 `dense_rank`、`bm25_rank`、两路原始 score 和 `rrf_score`，原始 score 只用于诊断，不参与 RRF。
- 同一个 `chunk_id` 若 Dense/BM25 返回的物理/业务身份不一致，Hybrid 直接报 integrity error。

当前正式 baseline：

```text
candidate_limit = 20
rrf_k = 60
final top_k = 5
```

### 3.4 Cross Encoder Reranker

```text
Dense Top candidate_limit ─┐
                           ├─ RRF → Hybrid Top rerank_depth
BM25 Top candidate_limit ──┘
                                      ↓
                         PostgreSQL authoritative text
                                      ↓
                              RerankerProvider
                                      ↓
                           Cross Encoder relevance score
                                      ↓
                              Final Top limit
```

当前实现：
- Provider contract：`query + documents -> list[float]`
- OSS adapter：Sentence Transformers `CrossEncoder`
- 正式模型：`BAAI/bge-reranker-v2-m3`
- Candidate 身份、Hybrid 原始 rank/score、稳定排序和 final Top-K 由 TechPilot 控制
- `RerankCandidate` 携带 PostgreSQL 权威正文；最终 `RerankedSearchHit` 不复制完整正文
- `RerankedSearchHit` 保留原 `HybridSearchHit`，因此 Dense/BM25/RRF diagnostics 不丢失
- 同分时保留原 Hybrid 顺序，保证 deterministic
- PostgreSQL Chunk 缺失或身份冲突时直接报 integrity error，不静默丢弃

深度边界：

```text
candidate_limit = 每一路 Retriever 的深度
rerank_depth    = RRF union 中真正送入 Cross Encoder 的深度
final limit     = Reranker 最终返回深度

final limit <= rerank_depth <= 2 * candidate_limit
```

Day 14 正式配置：`candidate_limit=20 / rerank_depth=20 / final limit=5 / rrf_k=60`。

Reranker 只能重新排序已经进入候选池的 Chunk，不能恢复 Dense/BM25 都未召回的目标。


### 3.5 当前生产边界

```text
AnswerService
  ↓
DenseRetrievalService        ← 当前生产路径

BM25RetrievalService         ← 独立实现 / evaluation path
HybridRetrievalService       ← 独立实现 / evaluation path
RerankingService             ← 独立实现 / evaluation path
```

Day 15 后 `AnswerService` 的 Retrieval 仍保持 Dense-only；BM25 / Hybrid / Reranker 仍是独立/evaluation path。变化只发生在可信回答边界：Context 构建后先经过 Evidence Verifier，只有 verified sufficient Evidence 才进入生成模型。

## 4. 可信问答链路

```text
POST /answers
  ↓
AnswerService
  ├── Workspace 校验
  ├── DenseRetrievalService
  ├── PostgreSQL Chunk 正文回查
  ├── Context Enricher
  ├── Context Builder
  ├── EvidenceVerifierProvider
  │     ↓
  │   sufficient / insufficient / conflicting
  │
  ├── insufficient / conflicting → Refused
  │
  └── sufficient
        ↓
      仅保留 verified supporting sources
        ↓
      DeepSeek Answer Provider
        ↓
      Answer + server-built Citation
```

关键约束：

- 完整正文必须从 PostgreSQL 回查。
- 已删除 Document 不得进入正文回查。
- Retrieval / Reranker 负责 relevance；Evidence Verifier 独立判断 Evidence Sufficiency。
- Verifier 只检查真实进入 `BuiltContext.sources` 的 Evidence；被 Context Budget 排除的来源没有验证资格。
- Verifier 的 `source_id` 必须来自实际输入 Evidence，未知 Source 直接报错。
- `INSUFFICIENT / CONFLICTING` 在生成模型调用前拒答，拒答权不依赖生成模型自报 confidence。
- `SUFFICIENT` 后，生成模型只能看到 Verifier 明确认可的 supporting sources，避免从未验证来源取事实后错误挂 Citation。
- LLM 只可使用内部 `SOURCE_N` 选择 Citation；`SOURCE_N` 不得泄漏到用户可见正文。
- Citation 只能绑定“真实进入 Context + Verifier 明确认可 + 生成模型实际引用”的来源。
- 生成模型若在 Verifier 已判 `SUFFICIENT` 后再次自行拒答，视为生成状态与 evidence state 不一致。
- Refused 不返回 Citation。

## 5. 删除链路

```text
DELETE /documents/{document_id}
  ↓
DocumentService
  ↓
PostgreSQL Soft Delete
  ↓
Commit
  ↓
Best-effort Qdrant Cleanup
```

关键约束：

- 先提交 PostgreSQL 删除状态，再清理 Qdrant。
- Qdrant Cleanup 失败不回滚事实来源。
- 长期可靠清理方案仍为 Outbox Pattern。

## 6. 评测链路

### Retrieval

```text
Current Legal Corpus Snapshot
        +
30-case Golden Dataset
        ↓
Strict Golden integrity check
        ↓
Dense Top-N + BM25 Top-N
        ↓
Single shared candidate snapshot
        ├── Dense Top-5 metrics
        ├── BM25 Top-5 metrics
        ├── RRF Fusion → Hybrid Top-5 metrics
        └── Hybrid rerank pool → Cross Encoder → Reranked Top-5 metrics
        ↓
Recall@5 / MRR@5 / Candidate miss / Rescue / Regression / Latency comparison
```

正式 Day 13 结果：

- Dense：Recall@5 0.700000 / MRR@5 0.500000 / MISS 9
- BM25：Recall@5 0.700000 / MRR@5 0.567778 / MISS 9
- Hybrid：Recall@5 0.766667 / MRR@5 0.588333 / MISS 7
- Dense-only hit：4
- BM25-only hit：4
- Both hit：17
- Both miss：5
- Dense/BM25 Top-5 hit union：25/30
- Hybrid actual hit：23/30
- Fusion loss：2

Day 14 正式 Reranker 结果：

- Hybrid+Reranker：Recall@5 0.866667 / MRR@5 0.766667 / MISS 4
- Rescues：3；Regressions：0；Retained Hybrid hits：23/23
- Rerank inference mean 2323.19 ms / P95 2956.97 ms
- Reranked total mean 3009.45 ms / P95 3699.76 ms
- `rerank_depth=40` 不增加 Recall/MRR，却把 inference mean 提高到 3893.50 ms，因此拒绝采用

Golden integrity 不只校验 `expected_chunk_id` 是否存在，还必须校验其与当前 workspace、active/legal Document、`expected_document_id`、名称、chunk index 和 section 的一致性。

评测运行记录必须绑定 dataset hash、corpus snapshot hash、git SHA 和检索配置。开发阶段若工作树尚未 commit，git SHA 只代表当前基线 commit，不能误写成包含未提交实验代码的实现 SHA。

### Answer

```text
Answer Cases
  ↓
answer_eval.py
  ↓
真实 AnswerService
  ↓
Answer correctness / Citation support / Refused / Runtime errors
```

Day 11 answerable 生产评测：4/7 answer correct、4/7 citation supported、1/7 over-refusal，因此 P1 Gate = FIX。

### Evidence Sufficiency

```text
Reviewed Evidence Cases
        ↓
EvidenceVerificationInput
        ↓
DeepSeekEvidenceVerifierProvider
        ↓
Pydantic structured validation
        ↓
provider-neutral invariant validation
        ↓
state / primary reason / source roles
```

Day 15 正式 reviewed local Golden：

- cases：6
- sufficient：2
- insufficient：3
- conflicting：1
- state accuracy：6/6
- primary reason exact match：6/6
- runtime errors：0
- prompt version：`evidence-verifier-v2`

规则：

- Golden 不能由 Verifier 输出自动回标。
- `insufficient` 只保留一个最小决定性 primary reason，避免把下游缺失重复计为多个 failure type。
- `subject_mismatch`：没有证据真正属于目标主体。
- `attribute_missing`：目标主体存在，但询问属性/值不存在。
- `relation_missing`：目标主体和属性/值都出现，但两者所需关系未建立。
- `conflicting_evidence`：证据对目标关系存在实质冲突。
- Evidence Verifier 输出本身不使用“模型 confidence”作为拒答 Gate。

## 7. Agent / Harness 边界

详细决策见 `docs/adr/ADR-001-agent-runtime.md`。

冻结原则：

```text
Thin Agent Control Layer
        ↓
Thick Harness
  ├── Tool Runtime
  ├── Context
  ├── Evidence / Verification
  ├── Trace / Evaluation
  └── Permission Boundary
```

Day 13–17 仍以 P2 为最高优先级，只允许 Harness 设计冻结，不实现 Agent Runtime。

v1 只读：

```text
Research / Understand / Analyze / Plan
```

`edit_file`、shell、worktree、自动代码修改等 Action 能力属于 v2 backlog。

## 8. 当前未实现能力

- 持久化 / 缓存化 BM25 lexical index
- OCR
- Outbox Pattern
- 流式上传和文件大小限制
- Code RAG
- Tool Registry / Agent Runtime

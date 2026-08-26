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

Day 17 production freeze 后，`AnswerService` 的 Retrieval 继续保持 Dense-only；BM25 / Hybrid / Reranker 保持独立 capability / evaluation path。

Production validation 依据：
- 7-case Answer A/B：Dense `3/7`，Hybrid `4/7`
- 当前 Hybrid Retrieval 平均增加约 `551 ms`
- 三个核心失败 target 均未进入 Hybrid Top-20 candidate pool
- Reranker 因此无法修复这些 candidate-generation failures

当前 Retrieval 已知限制收敛为 candidate generation / chunk-level evidence coverage；不继续对 RRF 或 rerank depth 做无边界调参。

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
- Provider 仅允许当前 request 内唯一、精确的 `source_ref -> source_id` 归一化；歧义或未知标识仍拒绝。
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

### Evaluation Run Identity

P2 正式 evaluation run 统一记录 `trace_id / config_version / git_sha / git_dirty`。`git_sha` 标识 baseline commit；若 `git_dirty=true`，不得将该 SHA 描述为包含全部本地实现的可复现版本。

Day16 ablation 分开报告 retrieval quality、reranker gain、evidence refusal behavior、latency/cost proxy；没有 token/货币 telemetry 时不推导虚构成本。

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

### 7.1 P3 read-only Code RAG / Harness foundation

Day18 开始将 ADR 中冻结的 Harness 边界落成可独立测试的基础设施：

```text
Future Thin Agent / Repo Explorer
        ↓
ToolRegistry
        ↓
ToolRuntime
  ├── input/output schema validation
  ├── risk permission
  ├── timeout
  ├── structured error
  └── latency / trace metadata
        ↓
Repository Tools
  ├── tree
  ├── read_file
  ├── search_code
  └── search_symbol
        ↓
RepositoryReadBoundary
        ↓
Repository
```

RepositoryReadBoundary 统一负责 repository root containment、path canonicalization / escape rejection、symlink rejection、excluded directory pruning、sensitive `.env*` exclusion、binary / oversized file rejection 和 deterministic traversal。Repository tools 不重复实现 filesystem permission 规则。

代码发现职责拆分：

```text
search_code = literal lexical discovery
search_symbol + Python AST = class / function / method structural discovery
CodeEvidence = authoritative code provenance
```

`search_symbol` v1 使用 exact `name` / exact `qualified_name` 语义，不因父级 class 名称自动展开全部成员。单文件 AST parse error 作为局部失败计数，不中止其他合法文件搜索。

`CodeEvidence` 最小 provenance 为 `repository / file_path / symbol / line_start / line_end / snippet`；`snippet` 只能从 `RepositoryReadBoundary` 允许的真实文件和 line range 重建。Search result 只表示候选定位，不直接等同 Evidence。

Day18 不实现 Repo Explorer / EvidencePack orchestration、Agent Control Layer 或 LangGraph。

### 7.2 EvidencePack / minimal Repo Explorer

Day19 在 Day18 的 repository tools 之上增加最小只读编排层：

```text
Repository understanding task
        ↓
RepoExplorer
        ↓
ToolRegistry
        ↓
ToolRuntime
   ├── search_symbol
   ├── search_code
   └── read_file
        ↓
RepositoryReadBoundary
        ↓
Repository
        ↓
CodeEvidence[]
        ↓
EvidencePack
```

职责边界：

- Repo Explorer 负责 deterministic capability orchestration 与 evidence handoff，不负责 Agent planning / reasoning loop。
- `search_symbol / search_code` 只产生 candidate location，不直接成为 Evidence。
- candidate 必须通过 `read_file` 重新取得 authoritative content；snippet 根据 authoritative file content + line range 构造。
- Explorer 构造器只接收 `repository / ToolRegistry / ToolRuntime`，不接收 filesystem root、`Path` 或 `RepositoryReadBoundary`，避免绕过 Harness permission boundary。
- `tree` 保持为可注册的只读 repository capability；当前 direct search flow 不为了形式强制调用。

最小 EvidencePack：

```text
query
task_intent
CodeEvidence[]
provenance_integrity
incomplete
issues[]
```

`provenance_integrity` 表示已交付 Evidence 是否仍能绑定 authoritative repository content；`incomplete` 表示本次 exploration 是否存在截断、解析失败、tool failure、evidence limit 等覆盖缺口。二者必须分开，避免把“来源可信”和“搜索完整”混为同一状态。

当前 Repo Explorer 的目的首先是 Context Isolation：仓库搜索中产生的候选和中间 Tool Result 不直接进入未来主模型上下文，只交付筛选后的 EvidencePack。

## 8. 当前未实现能力

- 持久化 / 缓存化 BM25 lexical index
- OCR
- Outbox Pattern
- 流式上传和文件大小限制
- Agent Runtime / Agent-level Trace

### 7.3 Lightweight AgentEvent trace

Day20 在现有 ToolRuntime / RepoExplorer 上增加 observability trace，而不是增加新的业务控制层：

```text
RepoExplorer
   │
   ├─ TOOL_CALL ──────┐
   ├─ TOOL_RESULT ────┤
   ├─ TOOL_CALL ──────┤── same trace_id
   ├─ TOOL_RESULT ────┤
   └─ EVIDENCE_HANDOFF┘
```

原则：

- Event trace 是运行过程记录，不是 EvidencePack 业务状态。
- `TOOL_CALL` 与 `TOOL_RESULT` 分开，支持定位调用前后失败点。
- Tool events 由 ToolRuntime 统一产生，RepoExplorer 不复制 Runtime 的工具执行职责。
- `EVIDENCE_HANDOFF` 由 RepoExplorer 在最终 EvidencePack 形成后产生。
- 输入输出只记录 summary，避免 trace 成为完整代码/敏感参数的冗余副本。
- Event sink 为 best-effort dependency；trace failure 不能改变业务结果。
- 当前 `InMemoryAgentEventSink` 仅用于测试 / demo，持久化留到后续真正需要时再决定。


### 7.4 Code retrieval / module / static call clues

Day21–25 将 Repo Explorer 的 repository-understanding capability 扩展为：

```text
Repository question
        ↓
RepoExplorer
        ↓
ToolRegistry / ToolRuntime
        ├── search_code_keyword
        ├── search_code_dense
        ├── search_code_hybrid
        ├── inspect_modules
        └── inspect_calls
        ↓
candidate locations / structural clues
        ↓
read_file
        ↓
authoritative CodeEvidence
        ↓
EvidencePack
```

能力职责：

- Keyword / Dense / Hybrid：回答“哪些代码块可能相关”。
- `inspect_modules`：回答 Python module、internal import dependency、top-level symbol 的静态结构。
- `inspect_calls`：回答 function/method 源码中可观察到的 caller/callee call-site clue。
- `read_file`：重新取得 authoritative repository content。
- `CodeEvidence`：只承载可回查的 file/symbol/line/snippet provenance。

Static call boundary：

```text
caller symbol
    ↓
AST Call
    ↓
Name / Attribute callee expression
    ↓
StaticCallClue
```

`StaticCallClue` 不是完整 runtime call graph。Python 的动态分派、dependency injection、多态、decorator、`getattr`、monkey patch 等不能仅凭 AST 完整恢复，因此系统只提供静态线索，并保留 incomplete/failure 语义。

结构线索和 Retrieval result 都不能直接成为 Evidence；最终仍统一经过 `read_file -> CodeEvidence -> EvidencePack`。P3 继续保持 read-only，不开放 shell、edit、git write 或 Agent control loop。

### Repository Structural Index（Day 27）

Code RAG 的结构能力分为两个阶段：

1. Repository refresh / indexing：
   - 读取允许范围内的 Python 文件；
   - AST parse；
   - 建立 module、internal imports、top-level symbols、static call clues；
   - 构建 query-time postings。

2. Query-time：
   - 根据 query 从 structural index 取得少量相关结构候选；
   - RepoExplorer 做 bounded candidate handling；
   - 最终必须通过 `read_file` 回查真实源码；
   - 只有真实源码生成的 `CodeEvidence` 才能进入 `EvidencePack`。

因此 structural index 是定位索引，不是事实来源。事实来源仍然是 repository 中当前允许读取的真实源码。

`.local/` 属于本地 review / diagnostics / backup / evaluation artifacts，不属于 runtime repository corpus，因此由 `RepositoryReadBoundary` 排除。

当前 structural index 为 in-memory full rebuild。该方案已经消除“每个用户 query 都重新扫描并 AST parse 全仓”的错误路径；incremental refresh / persistent index 留给后续规模化阶段。

<!-- DAY29_P3_FINAL_GATE -->
### P3 final retrieval architecture note (2026-08-16)

P3 uses a two-phase repository design:

1. refresh/index phase:
   safe repository traversal → AST/symbol extraction → structural snapshot →
   keyword/dense code chunks and vectors.
2. query phase:
   bounded lookup / retrieval → candidate set → authoritative `read_file` →
   CodeEvidence → EvidencePack.

The architecture deliberately separates **candidate discovery** from **evidence authority**.
Static module/import/call information assists localization but is not a runtime program graph.

External robustness evaluation showed representation/retrieval quality depends on repository
shape. A tested policy that dropped oversized whole-class chunks reduced external quality and
was reverted. Future representation improvements should preserve class-level context while
using hierarchical or summarized representations rather than deleting it outright.

Repo Explorer remains because it owns evidence handoff, failure/incomplete semantics and
structural context isolation. Its compression benefit is strongest in module/structure mode;
Dense/Hybrid external challenges showed no file-compression delta, so universal compression
must not be claimed.

<!-- DAY31_33_P4_ARCH_START -->
## P4 Research Execution Architecture — Day31–33

Day31–33 后，P4 主路径从早期 `PLAN / ACT / VERIFY` 语义分层收敛为：

```text
User Task
   ↓
Task Router
   ↓
Execution Strategy
   ├─ model tier
   ├─ step / retry budget
   ├─ agent autonomy
   └─ Evidence context strategy
   ↓
Unified Semantic Reasoner
   ├─ current Evidence sufficient?
   ├─ unresolved gap?
   └─ next bounded ResearchAction
   ↓
Deterministic Harness
   ├─ ToolRuntime schema / permission / timeout
   ├─ retry / max_steps / termination
   ├─ RepositoryReadBoundary
   └─ Candidate → authoritative Evidence
   ↓
EvidencePack
   └────────────→ Reasoner loop
```

### 语义与硬约束边界

LLM 可以决定：

- 当前 Evidence 是否足够；
- 还缺什么；
- 下一步想使用哪个允许的 capability。

LLM 不决定：

- permission；
- schema validity；
- timeout；
- repository safety；
- hard max steps / retry budget；
- Candidate 是否可以直接成为 Evidence。

原则：

> 语义决策集中给 LLM，硬约束集中给 Harness。

### 为什么保留 Unified Reasoner

Day32 实现曾将 Planner / Action Selector / Verifier 分开。最终主路径收敛为一个 Unified Reasoner，避免多个语义 LLM 对同一 State 产生不一致解释，同时减少额外 LLM call。

Day31 deterministic / layered implementation仍保留为 baseline / learning reference，不作为 P4 主 semantic path。

### Execution Strategy 分级

#### Workflow

- fixed execution path；
- no LLM；
- no Agent autonomy。

#### Light Agent

- DeepSeek Flash；
- `max_steps=2`；
- 单一明确 symbol 时采用 deterministic symbol-first；
- authoritative Evidence 进入 LLM 前使用 query-focused window；
- LLM 只承担必要的 semantic sufficiency / gap decision。

#### Research Agent

- DeepSeek Pro；
- `max_steps=5`；
- dynamic action selection；
- 当前仍使用 prefix context baseline，等待 multi-source / conflict / long-Evidence workload 后再决定 context strategy。

### Evidence Context 成为一等架构变量

Day33 证明：

```text
correct source
≠
sufficient decision context
```

因此 Context 不再只用“最大 token 数”描述，还必须考虑“选哪部分 Evidence”。

P4 evaluation 应明确区分：

- Source Coverage：是否找到了正确权威来源；
- Decision Context Coverage：当前 LLM 实际可见片段是否覆盖任务所需事实；
- Grounded Completion：COMPLETE 是否被当前可见 Evidence 支撑。

这与既有 Candidate / Evidence / Trace 边界兼容，不改变 authoritative Evidence 本身；只改变一次 semantic decision 时从 EvidencePack 选择哪部分进入 model context。
<!-- DAY31_33_P4_ARCH_END -->

<!-- P4-DAY34-37-ARCH-START -->
## P4 Day34–37：Research Agent production-facing contracts

### 1. Final control/data flow

```text
User Task
→ Task Router
→ Execution Profile
→ Unified Semantic Reasoner
→ ResearchAction
→ Deterministic Control / ToolRuntime
→ RepoExplorer / Tools
→ Candidate
→ authoritative materialization
→ EvidencePack
→ ActionExecutionOutcome
→ Reasoner
→ DecisionReportFinalizer
```

核心边界：

```text
semantic decision → LLM
hard constraint   → Harness
fact              → authoritative Evidence
history/debug     → Trace
quality judgment  → Evaluation
```

### 2. Action outcome contract

Primitive `ToolResult` 不能代表 composite capability 的业务结果。

`RepoExplorerActionExecutor` 可以成功返回 EvidencePack，同时 `last_tool_result=None`。

因此 Reasoner 读取独立 action-level outcome：

```text
capability
tool_result_present
tool_result_ok
evidence_returned_count
new_evidence_count
issue_count
retry_count_after
termination_reason
```

避免：

```text
last_tool_result == null
⇒ previous action had no result
```

这一错误推断。

### 3. Failure ownership

Provider：

```text
classify failure
→ code / retryable / status_code
```

Control Layer：

```text
decision_retry_count
max_decision_retries
retry / stop
RETRY_EXHAUSTED
PERMANENT_FAILURE
```

Composite capability 必须向 control layer 传播 current-action retryable failure，不能只保留在 domain issue 中。

### 4. Known-path semantics

`search_mode="path"` 只接受 exact repository-relative file path。

```text
known file
→ RepositoryReadBoundary
→ ToolRuntime
→ read_file
→ authoritative Evidence
```

Known path 不做 fuzzy retrieval；read failure 不用相似文件替代。

### 5. Source-role Evidence

Reasoner context 显式标记：

```text
app/     production
tests/   test
docs/    documentation
scripts/ script
```

对 implementation claim：

> tests 可 corroborate，但不能替代 production implementation Evidence。

### 6. Termination invariants

- `max_steps` 表示 ACT execution budget；
- 最后一个 ACT 后允许 final semantic decision；
- `NO_ACTIONABLE_PATH` 必须仍有 unresolved obligation；
- duplicate ACT 在无 retry failure 时拒绝；
- bounded repair exhaustion 结构化终止，不允许 graph crash。

### 7. Delivery

`DecisionReportFinalizer`：

- COMPLETE → evidence-grounded user-facing conclusion + Sources；
- incomplete → termination / reason / unresolved + Sources。

因此：

```text
research success != delivery success
```

### 8. Evaluation boundary

Agent evaluation 不只看 final answer 或 target file：

```text
task_success
source_coverage
decision_context_coverage
semantic_requirement_coverage
provenance_integrity
tool_selection
control correctness
termination
recovery
LLM calls
tokens
latency
cost
```

realistic noise 与 benchmark contamination 分离。当前实验直接派生并包含 expected-answer clue 的 regression files 不进入 canonical corpus。

### 9. P4 known architectural limits

- semantic source/query planning；
- multi-obligation decomposition；
- obligation expansion；
- external-source joint research；
- production API / service-level long-task behavior。

这些属于后续真实业务/产品演进，不在 P4 继续用特例调优。
<!-- P4-DAY34-37-ARCH-END -->

<!-- DAY37_5_PRODUCT_UI_START -->
## Product UI boundary (Day37.5)

```text
Browser
  ↓
FastAPI Product UI (`app/product_ui`)
  ├─ Workspace lifecycle → /workspaces
  ├─ Grounded Q&A       → /answers
  ├─ Source ingestion    → /documents/upload
  ├─ Source deletion     → /documents/{id}
  └─ Dependency health   → /health/dependencies
```

Day37.5 的 UI 是 delivery / interaction layer，不是新的 Agent runtime。它不得绕过现有 service、Evidence、permission 或 persistence boundary。

Workspace lifecycle：

```text
create(name)
→ PostgreSQL Workspace
→ database-assigned id

select
→ browser active workspace state
→ downstream requests carry workspace_id

delete(workspace)
→ active document count > 0 ? 409 : delete
```

删除非空 Workspace 选择 fail-closed，避免数据库 Workspace 被删除后仍存在概念上未清理的 active indexed source。

Knowledge Base 当前没有 persistent listing API，因此 UI 只能将当前浏览器 session 的 upload responses 作为 source list；这是产品能力边界，不以 mock 数据补齐。

前端目录统一为 `app/product_ui/`，替代 Day37.5 迭代期的 `app/web/`。
<!-- DAY37_5_PRODUCT_UI_END -->

<!-- P5_DAY38_ARCH_20260824_START -->
## P5 Job/JD boundary (Day38)

P5 当前采用独立 domain workflow，不创建第二套 Agent runtime。

```text
User goal / query
      ↓
QueryParser
      ↓
UserJobIntent / JobSearchSpec
      ↓
JobDiscoveryProvider
      ↓
RawJobResult
      ↓
JobDiscoveryPipeline
  ├─ normalization
  ├─ quality filter
  └─ deduplication
      ↓
JobRecord
      ↓
JobJDService
      ↓
JDExtractor
      ↓
StructuredJD
  └─ JDRequirement[]
       ├─ normalized_skill
       ├─ category
       ├─ requirement_type
       ├─ importance
       └─ EvidenceSpan(text/start/end)
```

Optional matching：

```text
StructuredJD requirements
        +
UserCapabilityProfile
        ↓
JobMatcher
        ↓
JobRanker
        ↓
JobRecommendation
```

### JD model boundary

```text
LLM raw response
→ JSON decode
→ Pydantic validation
→ one bounded structural repair when eligible
→ source evidence binding validation
→ StructuredJD / structured failure
```

Model-generated normalized fields never replace authoritative JD source text.

### Job Discovery boundary

`JobDiscoveryProvider` owns external discovery integration. Domain service only consumes normalized `JobRecord`。

Mock providers are test doubles only and must not be wired as production default behavior。

### Explicit non-coupling

P5 Job/JD domain does not import or emit:

- `EvidencePack`
- `CodeEvidence`
- repository Code RAG
- Research Agent state/runtime

This separation is permanent for P5: repository Code RAG is not a dependency, optional enrichment, evidence source, or future matching stage for job discovery, resume recommendation, or Resume↔JD fit analysis.

### Cross-cutting infrastructure corrections

- Qdrant loopback requests bypass environment HTTP proxies; remote URLs retain normal proxy behavior.
- dependency PostgreSQL health probe uses isolated `NullPool`, avoiding contamination of the application async connection pool.
- production PDF routing retains OCR threshold while direct parser contract remains backward compatible.
<!-- P5_DAY38_ARCH_20260824_END -->

<!-- TECHPILOT_JOB_INTELLIGENCE_ARCH_START -->
## Job Intelligence architecture boundary — frozen 2026-08-24

Job Intelligence is architecturally independent from Code RAG.

```text
Job Intelligence
  intent/resume/JD
  -> job-source providers
  -> JD structured extraction
  -> evidence binding
  -> profile/JD matching
  -> ranking/explanation

Code RAG
  repository query
  -> repository search/structure
  -> authoritative read_file
  -> CodeEvidence/EvidencePack
```

No cross-flow from JD requirements into repository evidence is part of P5.

Real-source providers exposed an additional boundary:

```text
source acquisition health
!=
JD extraction quality
!=
matching quality
```

Source failures must remain observable and must not collapse into `no match`.

The BOSS browser connector is recorded as an experiment, not a production source. v8 demonstrated listing capture only; v9 was not validated.

The next candidate architecture direction is AI Coding. Do not add write/shell/patch capabilities until the product thesis versus mature coding agents is frozen.
<!-- TECHPILOT_JOB_INTELLIGENCE_ARCH_END -->

<!-- P6_PRODUCTION_ARCH_20260827_START -->
## P6 Production Architecture — Current Superseding Layer

> 本节 supersede 上方历史“当前生产边界”描述。上方 Day13–17 Dense-only / BGE 等内容保留为当时 architecture history；P6 当前 production composition 以本节为准。

### 1. Identity / workspace authorization

```text
Bearer JWT / HttpOnly cookie
→ active User
→ AuthPrincipal
→ WorkspaceAuthorizer
→ workspace_member
→ workspace-scoped service
```

request 中的 `workspace_id` 只是资源标识，不是 permission proof。Unauthorized workspace 主要 fail-closed 为 404。

### 2. Current ingestion

```text
PDF / Markdown / DOCX
→ ParserRouter
→ StructureAwareChunker
→ Document + Chunk PostgreSQL commit
→ IndexingService
→ Qdrant
```

DOCX：python-docx + paragraph/table body order + Heading 1–6 section path + OOXML ZIP preflight。当前不做 DOCX image understanding / header-footer / full nested layout semantics。

### 3. Searchable authoritative boundary

Answer-side `ChunkRepository` 与 BM25 均要求：

```text
workspace match
AND deleted_at IS NULL
AND Document.status IN (COMPLETED, PARTIAL)
```

这使 FAILED document 即使存在 orphan Qdrant point，也无法成为 authoritative Answer evidence。

### 4. Current production retrieval

```text
Dense multilingual-e5-base ─┐
                             ├─ RRF Hybrid
BM25 ────────────────────────┘
           ↓
CrossEncoder reranker
           ↓
AnswerRetrievalAdapter
```

Production reranker：

```text
cross-encoder/mmarco-mMiniLMv2-L12-H384-v1
```

Strict 30-case P2 same-environment comparison：

```text
old ms-marco MiniLM: Recall .666667 / MRR .578889 / regressions 5 / ~147ms
multilingual MiniLM: Recall .866667 / MRR .740000 / regressions 0 / ~253ms
BGE v2-m3: Recall .866667 / MRR .766667 / regressions 0 / ~2320ms
```

BGE offline MRR 略高，但 multilingual MiniLM 在相同 Recall/0-regression 下约 9.2x 更快，因此作为 online default。

### 5. Trusted answer + verifier-driven recovery

```text
/answers
→ auth / workspace authorization
→ optional idempotency + conversation snapshot/version
→ release DB transaction before slow provider path
→ retrieval / authoritative materialization
→ Evidence Verifier #1
   ├─ SUFFICIENT  → verified sources → generator → server citation
   ├─ CONFLICTING → refuse
   └─ INSUFFICIENT
        ↓
      bounded structural recovery
        ├─ recovery top-N anchors
        ├─ prefer novel parent sections
        ├─ exclude existing anchors from additions
        └─ selected-anchor ±1 boundary bridge
        ↓
      Evidence Verifier #2
        ├─ SUFFICIENT → generate
        └─ otherwise  → refuse
```

Boundary bridge 保留 chunk 真实 section；只用局部结构邻接发现跨章节 evidence，不伪造 provenance。

### 6. Conversation concurrency

```text
read conversation/version V
→ release transaction
→ slow answer
→ append_exchange_if_version(V)
```

version changed → HTTP409，stale answer 不写入、无 half-turn。

### 7. Idempotency

```text
scope = user + workspace + operation + key + request_hash
state = PROCESSING / COMPLETED / FAILED
```

- completed same request → exact replay；
- same key different request → conflict；
- failed same request → may retry。

不声称 exactly-once；ingestion PG commit 与 idempotency completion 之间有 narrow crash window。

### 8. Failure recovery

- provider transient retry bounded；
- exhaustion 后 request failure + idempotency FAILED；
- conversation provider failure 不留 half exchange；
- CAS stale output 不落库；
- Qdrant partial indexing 做 compensation delete；
- compensation 失败时仍由 PG searchable-state gate fail-closed。

P6 live DB probe 已验证 `FAILED → same-key retry → COMPLETED`，以及 post-commit indexing failure 下 persisted chunks 对 Answer/BM25 non-searchable。

### 9. Current known limits

- P6 CAS test 不是高并发 load test；
- Qdrant operational cleanup 不是 distributed transaction；
- auth rate limit / lockout / explicit CSRF 等仍有 backlog；
- DOCX multimodal/layout coverage 不完整；
- destructive authorization 未逐条 live attack 所有真实资源；
- semantic-gap recovery 是 bounded policy，不宣称 universal recall。

### 10. Next: P7 service-level validation

下一阶段测试 throughput、P50/P95/P99、DB pool saturation、reranker/provider contention、retry amplification、backpressure/rate limiting、timeout/cancellation/cleanup 与 graceful degradation。
<!-- P6_PRODUCTION_ARCH_20260827_END -->

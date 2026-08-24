# TechPilot PROJECT_STATUS

## 当前版本

v0.7-dev

## 当前阶段

P5 Day38 engineering contract 已通过全量回归；真实 JD / 真实岗位发现评测尚未闭合。Day38 = CONDITIONAL PASS，P5 Gate = OPEN。

## 阶段状态

- P0 工程骨架：完成
- P1 文档 RAG：工程闭环完成，但质量 Gate = `FIX`
- Day 11：P1 answerable 质量复验完成，定位检索召回为主要瓶颈
- Day 12：BM25 实现、正式评测与 Dense/BM25 对比完成
- Day 13：RRF Hybrid Retrieval、真实评测、失败分析与 Agent Runtime ADR 完成
- Day 14：Cross Encoder Reranker、30-case 质量/延迟实验、候选深度边界修正与 ToolContract/ToolResult 字段冻结完成
- Day 15：Evidence Verifier、evidence-driven refusal、正式 6-case Evidence Eval 与未来 Tool Schema 冻结完成
- P2 高质量 RAG：capability Gate = PASS；production Retrieval = Dense-only
- P3 Code RAG：Day 18–25 repository boundary / Repo Explorer / trace / code retrieval / module structure / static call clues 已完成
- P4 Research Agent：Day30–37 = PASS WITH KNOWN LIMITATIONS
- Evaluation Backfill：COMPLETE
- P5 Day38：engineering contract = PASS；real-business validation = OPEN；overall = CONDITIONAL PASS
- 下一步：连接真实 Job Discovery source，建立真实 JD seed 并完成 extraction evaluation

## 已完成

### P0 / P1 核心能力

- FastAPI、PostgreSQL、Redis、Qdrant、SQLAlchemy、Alembic 工程骨架
- Markdown / 文本型 PDF 摄取与结构优先 Chunking
- 稳定 Chunk ID 与 PostgreSQL 权威正文存储
- `multilingual-e5-base` Dense Retrieval 与 Workspace 隔离
- DeepSeek 可信回答、Citation 绑定、证据不足拒答
- Document 软删除、Qdrant Best-effort Cleanup 与删除后隔离
- Upload -> Persist -> Index -> Retrieve -> Answer -> Cite -> Delete -> Refuse 生命周期验证

### Day 11：P1 质量复验

- 建立 7 条 `answerable=true` 生产评测样本
- Answer correctness：4/7
- Citation support：4/7
- Over-refusal：1/7
- Runtime errors：0
- 三个失败目标 Dense rank：56 / 1120 / 26
- 生产 Prompt 增加 `SOURCE_N` 输出隔离规则
- P1 Gate 最终更新为 `FIX`
- 结论：继续改 Prompt 不能弥补权威 Chunk 未进入 Top-K，后续优先做检索优化

### Day 12：BM25 Baseline

- 新增 BM25 tokenizer、DTO、Repository、Retrieval Service
- 中英混合 tokenizer 保留技术标识符和数字，中文使用 jieba
- 配置 `bm25_k1=1.5`、`bm25_b=0.75`
- BM25 合法候选集限定为：当前 Workspace、`deleted_at IS NULL`、Document 状态 COMPLETED/PARTIAL
- BM25 与 Dense 使用同一 Chunk 身份空间
- 新增 tokenizer、评分、参数、limit、Workspace 和语料过滤测试
- 新增 `scripts/bm25_retrieval_eval.py`
- 完整自动化回归：PASS

### Day 13：RRF Hybrid Retrieval

- 新增纯 RRF fusion：只使用 rank，不混合 Dense/BM25 原始 score
- 使用统一 `chunk_id` 做跨 Retriever dedupe；同一路重复 Chunk 不重复贡献
- 新增 `HybridSearchHit`，保留 `dense_rank`、`bm25_rank`、两路原始 score 与 `rrf_score`
- 新增独立 `HybridRetrievalService`
- 明确 `candidate_limit` 与最终 `limit` 的职责边界
- 对 Dense/BM25 共享 Chunk 做 identity consistency 校验
- 新增 RRF 与 Hybrid Service 自动化测试
- 新增 `scripts/hybrid_retrieval_eval.py`
- 评测脚本先校验 Golden 与当前 legal corpus，再运行 Dense/BM25/RRF
- 真实 Hybrid Service smoke：PASS
- 完整自动化回归：154 passed
- `git diff --check`：PASS
- 生产 `AnswerService` 保持 Dense-only；Day 13 未提前接入 Hybrid
- 新增 `docs/adr/ADR-001-agent-runtime.md`，冻结 Thin Agent / Thick Harness 与 v1/v2 边界；未实现 Agent Runtime

### Day 14：Cross Encoder Reranker

- 新增稳定 `RerankerProvider` Protocol，第三方模型只负责 `query + documents -> relevance scores`
- 新增 Sentence Transformers `CrossEncoder` adapter，正式模型使用 `BAAI/bge-reranker-v2-m3`
- `max_length=512`，正式实验设备为 Apple MPS
- 新增 `RerankCandidate` / `RerankedSearchHit`，保留原始 Hybrid diagnostics 与 reranker score/rank
- 新增独立 `RerankingService`
- Reranker 正文只从 PostgreSQL 权威 `Chunk` 回查，不把完整正文塞入 Qdrant / Hybrid DTO
- 新增 Provider、DTO、Service focused tests 与真实模型 smoke
- 修正深度语义：`candidate_limit` 是 Dense/BM25 各自召回深度；fusion/rerank pool 理论上最多为 `2 * candidate_limit`
- 正式配置冻结为 `candidate_limit=20 / rerank_depth=20 / top_k=5 / rrf_k=60`
- 30 条 strict Golden 上，Hybrid+Reranker：Recall@5 `0.866667`、MRR@5 `0.766667`、MISS 4
- 相比 Hybrid：Recall@5 绝对提升 `+0.100000`，MRR@5 提升 `+0.178334`
- Reranker rescue 3 条：case 11 `7 -> 1`、case 15 `9 -> 1`、case 26 `11 -> 2`
- Regression：0；原 Hybrid Top-5 命中保留 `23/23`
- `rerank_depth=40` 有界诊断没有新增质量收益，但显著增加延迟，因此拒绝采用
- 完整自动化回归：183 passed
- `git diff --check`：PASS
- 生产 `AnswerService` 仍保持 Dense-only；Day 14 没有因离线质量提升直接切生产路径
- ADR 冻结 `ToolContract` / `ToolResult` 最小字段，不实现 Tool Registry / Agent Runtime

### Day 15：Evidence Verifier / Evidence Sufficiency

- 新增 provider-neutral `EvidenceVerifierProvider`
- 新增 Pydantic `EvidenceItem` / `EvidenceVerificationInput` / `EvidenceVerificationResult`
- Evidence provenance 使用 `source_type / source_ref / title / locator / text`，不把 Verifier 契约绑死在 Document Chunk，可直接映射未来 Code/Web evidence
- Evidence state 固定为 `sufficient / insufficient / conflicting`
- 不充分原因固定为 `no_evidence / subject_mismatch / attribute_missing / relation_missing / conflicting_evidence`
- `insufficient` 采用单一 primary reason，避免主体错误继续级联为属性/关系缺失
- Prompt version 冻结为 `evidence-verifier-v2`
- Verifier 输出拒绝未知 Source、Source role 重叠、重复 Source、额外字段以及非法 state/reason 组合
- 生产 `AnswerService` 保持 Dense-only Retrieval，但生成前新增 Evidence Verifier gate
- `INSUFFICIENT / CONFLICTING` 在生成模型调用前直接拒答
- `SUFFICIENT` 后生成模型只能看到 Verifier 明确认可的 supporting sources
- 最终 Citation 只能绑定 Verifier 标记为 supporting 且真实进入 Context 的来源
- 生成模型若在 Verifier 判定 `SUFFICIENT` 后再次自行拒答，视为状态不一致错误，而不是合法 evidence decision
- 新增真实 DeepSeek smoke 和独立 Evidence Verifier evaluation
- 6-case reviewed local Golden 覆盖 2 sufficient / 3 insufficient / 1 conflicting
- 正式 Evidence Eval：state 6/6、primary reason 6/6、runtime errors 0
- 完整自动化回归：227 passed
- `git diff --check`：PASS
- Evidence Verifier I/O 已可导出 JSON Schema，满足未来 `verify_evidence` Tool Schema 复用要求
- Day 15 未实现 Tool Registry / Agent Runtime / Repo Explorer / Coding Agent

### Day 16：P2 Ablation + Trace Identity

- 固定最小 ablation matrix，不继续对 30 条 Golden 做无界参数搜索。
- Dense `0.700000/0.500000`；BM25 `0.700000/0.567778`；Hybrid `0.766667/0.588333`；Hybrid+Reranker `0.866667/0.766667`（Recall@5/MRR@5）。
- Reranker vs Hybrid：Recall `+0.100000`、MRR `+0.178333`、rescues=3、regressions=0、retained=23/23。
- Reranker added latency mean `2318.09 ms` / P95 `2962.15 ms`；total mean `2990.90 ms` / P95 `3684.15 ms`。
- Evidence ablation：legacy non-empty gate accuracy `0.333333`、unsafe_accepts=4；Evidence Verifier accuracy `1.000000`、unsafe_accepts=0、over_refusals=0。
- Cost proxy：Generator calls `6 -> 2`，avoided=4；无 token/美元 telemetry，不伪造货币成本。
- Trace identity 已持久化到 `.local/days/day16/p2_ablation_summary.json`；baseline SHA `237780b6c8ab507a1d4dc95d94dc21b73eb1552d`，run 时 `git_dirty=true`。
- Full suite：234 passed；`git diff --check`：PASS。
- Day17 已完成 P2 Gate：`PASS`。

### Day 17：P2 Gate + Production Freeze

- P2 capability Gate：`PASS`
- v0.2-rag production Retrieval：继续 `Dense-only`
- Hybrid Answer A/B：`3/7 -> 4/7`
- Hybrid request-time Retrieval 平均增加约 `551 ms`
- P1 三个核心失败 target 均未进入 Hybrid Top-20 candidate pool
- 因此 Reranker 无法修复这些 candidate-generation failures
- Evidence Verifier source identity bug 已修复并增加 4 条回归测试
- full pytest：PASS；`git diff --check`：PASS
- P3 Harness backlog 已冻结；Day 17 不实现 P3

### Day 18：P3 Read-only Code RAG / Harness Foundation

- 新增 `RepositoryReadBoundary`，以 repository root 作为 runtime trust boundary。
- canonical path 必须保持在 root 内；拒绝 absolute/path escape、symlink、binary、oversized file。
- traversal 在进入目录前剪枝 `.git/.venv/venv/node_modules/cache/generated`，并排除 `.env/.env.*`；保留 `.env.example/.env.sample` 模板。
- 新增 minimal `ToolContract / ToolResult`、`ToolRuntime`、`ToolRegistry`。
- ToolRuntime 默认只允许 `READ / COMPUTE`，WRITE / DESTRUCTIVE 在 Harness 层拒绝。
- 新增首批 read-only repository tools：`tree / read_file / search_code / search_symbol`。
- `search_code` 只做 deterministic literal lexical matching。
- 新增 Python AST service；`search_symbol` 只识别 class/function/method，使用 exact name / exact qualified_name semantics。
- 单个 Python parse error 不终止全仓库 symbol search，通过 `parse_error_count` 暴露局部失败。
- 新增 `CodeEvidence`，最小 provenance 为 `repository / file_path / symbol / line_start / line_end / snippet`。
- snippet 根据安全 file path + line range 从 authoritative repository content 重建，不接受模型自由生成 provenance。
- Repository/Harness focused tests：34 passed。
- Full pytest：PASS。
- `git diff --check`：PASS。
- 既有 Starlette TestClient/httpx deprecation warning：非阻塞。
- Day18 继续遵守 v1 read-only boundary；没有 LangGraph、shell、edit_file、git write、worktree 或自动修复。

### Day 19：EvidencePack + minimal Repo Explorer

- Day18 `ToolResult.truncated` propagation 增加 regression test，统一 Harness outer result 与 tool payload truncation 语义。
- 新增最小 `EvidencePack`：`query / task_intent / evidence / provenance_integrity / incomplete / issues`。
- `issues` 使用结构化 taxonomy，覆盖 tool unavailable、tool failure、tool truncation、AST parse error、evidence limit、provenance mismatch。
- 新增 deterministic `RepoExplorer`，只通过 `ToolRegistry + ToolRuntime` 调用 repository tools。
- `search_code / search_symbol` 仅提供 candidate location；最终 `CodeEvidence` 必须再次经 `read_file` 获取 authoritative repository content 后构造。
- Explorer 不接收 `Path` 或 `RepositoryReadBoundary`，避免直接 filesystem bypass。
- 同一文件的 authoritative read 在单次 explore 内复用，避免重复 read_file 调用。
- `provenance_integrity` 与 `incomplete` 分离：合法 evidence 可以保持来源完整，同时 exploration 仍可因 truncation / parse error / tool failure 标记为不完整。
- 未引入 LangGraph、Agent Control Layer、shell、edit_file、git write、worktree 或自动修复。
- Full pytest：PASS。
- `git diff --check`：PASS。
- 既有 Starlette TestClient/httpx deprecation warning：非阻塞。


### Day 25：Static call relationships

- 新增 `PythonCallRelationshipService` 与 `StaticCallClue`。
- 新增 read-only `inspect_calls` Tool，并通过 ToolRuntime 统一执行。
- Repo Explorer 新增 `call` mode。
- call clue 只作为 static discovery/structure signal；最终证据仍由 authoritative `read_file` 重建为 `CodeEvidence`。
- 不声称构建完整 runtime call graph；动态绑定/DI/多态等保持明确限制。
- focused tests：PASS。
- full pytest：PASS。
- `git diff --check`：PASS。
- Starlette TestClient/httpx deprecation warning：既有非阻塞项。
- 下一步：Day26 建立 Code RAG evaluation set，评测文件命中、结构问题覆盖和 Explorer 上下文噪声。

## 验收证据

### Golden Dataset Integrity

Day 12 曾发现原 30 条 Golden 中有 6 条指向已删除旧文档，完成 corpus / label drift 修复。

Day 13 又发现一条 Golden 的 `expected_document_id` metadata 错误。Day 12 的 Retrieval HIT/MISS 实际只按 `expected_chunk_id` 判定，因此修复后 Dense/BM25 指标完全复现；但这暴露出 Day 12 的 Golden integrity check 对完整身份关系校验不足。

Day 13 已将正式前置校验收紧为：

- 当前 `workspace_id`
- 当前 legal Document：`deleted_at IS NULL`
- Document 状态 COMPLETED/PARTIAL
- `expected_document_id`
- `expected_document_name`
- `expected_chunk_id`
- `expected_chunk_index`
- `expected_section`

修复后：

- Golden：30/30 valid
- legal documents：5
- legal chunks：1153
- dataset SHA-256：`e65e8490ef8e23018673712b2e595c6779e842094bd49d5d433fc5641bcef7f5`
- corpus snapshot SHA-256：`1d393523789b235bcfc1f821491bf86c5bcd29f47e04da7ebb85362b9ad81b0e`

规则：任何 Retrieval Benchmark 前必须先检查 Golden 与当前 legal corpus snapshot 的一致性；不得用 Retriever 输出自动重标 stale case。

### Dense / BM25 / Hybrid（当前 corpus / 当前 Golden）

正式 Day 13 baseline：

| Retriever | Recall@5 | MRR@5 | MISS |
| --- | ---: | ---: | ---: |
| Dense | 0.700000 | 0.500000 | 9 |
| BM25 | 0.700000 | 0.567778 | 9 |
| Hybrid RRF | 0.766667 | 0.588333 | 7 |

正式 Hybrid 配置：

```text
candidate_limit = 20
top_k = 5
rrf_k = 60
```

Dense/BM25 Top-5 失败集合：

- Dense-only hit：4
- BM25-only hit：4
- Both hit：17
- Both miss：5
- 单路 Top-5 命中并集：25/30，理论 oracle upper bound = 0.833333

Hybrid：

- 实际命中：23/30
- Dense-only preserved：3/4
- BM25-only preserved：3/4
- Fusion losses：2
- Both candidate miss（Top-20）：3
- Hybrid rescues from both Top-5 miss：0

失败分析：

- case 11：Dense rank 2、BM25 candidate miss；较大 `k` 下被多条双路中等排名候选压出 Hybrid Top-5
- case 09：Dense rank 3、BM25 rank 14；较小 `k` 会过度强化单路头部，反而损失该跨路共识命中
- case 15：BM25-only rank 5，在双路候选合并后的 final Top-5 中天然脆弱
- case 25：BM25 candidate rank 17、Dense Top-20 miss，属于候选召回/排序问题，不是 RRF 能自动修复的问题

有边界参数实验：

| candidate_limit | rrf_k | Hybrid Recall@5 | Hybrid MRR@5 | 结论 |
| ---: | ---: | ---: | ---: | --- |
| 20 | 60 | 0.766667 | 0.588333 | 正式 baseline |
| 10 | 60 | 0.766667 | 0.595556 | 不增 Recall，candidate both-miss 由 3 增至 4 |
| 20 | 20 | 0.766667 | 0.588333 | 与 k=60 Top-5 结果相同 |
| 20 | 1 | 0.766667 | 0.586111 | 不增 Recall，fusion loss 从 case 11 转移到 case 09 |

结论：RRF 确认利用了 Dense/BM25 的互补性，但不能保证达到单路成功集合的 oracle union；继续针对 30 条 Golden 搜索 `k` 会造成过拟合，因此停止调参。

### Dense / BM25 / Hybrid / Hybrid+Reranker（Day 14）

同一份 30-case strict Golden、同一 legal corpus：

| Pipeline | Recall@5 | MRR@5 | MISS |
| --- | ---: | ---: | ---: |
| Dense | 0.700000 | 0.500000 | 9 |
| BM25 | 0.700000 | 0.567778 | 9 |
| Hybrid RRF | 0.766667 | 0.588333 | 7 |
| Hybrid + Reranker | 0.866667 | 0.766667 | 4 |

正式 Reranker 配置：

```text
model = BAAI/bge-reranker-v2-m3
candidate_limit = 20
rerank_depth = 20
top_k = 5
rrf_k = 60
max_length = 512
device = mps
```

质量变化：
- rescues：3
- regressions：0
- retained Hybrid Top-5 hits：23/23
- source both-candidate miss：3
- rerank candidate miss：4
- case 25 的目标在 RRF full rank 29，`rerank_depth=20` 时不会进入 Cross Encoder

正式 latency（模型 warm-up 后）：

| Stage | Mean | P50 | P95 |
| --- | ---: | ---: | ---: |
| Hybrid candidate | 682.99 ms | 666.42 ms | 755.31 ms |
| PostgreSQL fetch | 3.22 ms | 3.05 ms | 3.72 ms |
| Rerank inference | 2323.19 ms | 2351.03 ms | 2956.97 ms |
| Rerank added | 2326.42 ms | 2354.05 ms | 2960.38 ms |
| Reranked total | 3009.45 ms | 3043.61 ms | 3699.76 ms |

有界 `rerank_depth=40` 诊断：
- Recall@5 / MRR@5 与 depth=20 完全相同：`0.866667 / 0.766667`
- case 25 虽进入 rerank pool，仍未进入最终 Top-5
- Rerank inference mean `3893.50 ms` / P95 `5850.06 ms`
- Reranked total mean `4616.67 ms` / P95 `6562.46 ms`
- 结论：扩大到完整 RRF union 没有额外质量收益，正式配置保留 depth=20

生产判断：质量收益明确，但当前 Cross Encoder 增量 mean 约 `2.33s`、P95 约 `2.96s`，不直接作为交互式生产默认路径。

### 测试

- RRF focused tests：PASS
- Hybrid Service tests：PASS
- Hybrid real-service smoke：PASS
- Reranker Provider / Service focused tests：PASS
- Reranker real-model smoke：PASS
- Evidence Verifier real DeepSeek smoke：PASS
- Evidence Verifier formal eval：6/6 state + 6/6 primary reason
- Full Test Suite：227 passed
- `git diff --check`：PASS
- 非阻塞警告：Starlette TestClient / httpx deprecation warning
- HF Hub 未认证警告不影响当前 smoke 结果

## 架构文档

系统架构、数据流和 Dense/BM25/Hybrid 当前边界统一维护在 `docs/ARCHITECTURE.md`。

Agent 工程边界维护在 `docs/adr/ADR-001-agent-runtime.md`；Day 13 只做设计冻结，不启动 Agent Runtime。

## 已知非阻塞问题

- 生产 `AnswerService` 仍使用 Dense Top-K；Hybrid / Reranker 当前是独立 Retriever / evaluation path
- Hybrid+Reranker 仍有 4/30 MISS，其中 3 条在 Dense/BM25 Top-20 候选中都未出现
- 当前 Cross Encoder 在 MPS 上增加约 2.33s mean / 2.96s P95，尚不适合作为无条件生产默认路径
- RRF final Top-K 会产生 fusion truncation loss，不能等同于 Dense/BM25 oracle union
- BM25 当前按合法 PostgreSQL Chunk 集构建请求级 baseline，尚未做持久化 lexical index / cache
- Context Budget 使用字符数，不是真实 tokenizer token 数
- 仅支持文本型 PDF，不支持 OCR
- Qdrant 删除采用 Best-effort Cleanup
- Starlette/httpx TestClient 弃用警告仍存在
- PR #2 仍为 Draft / Open / 未合并

## 下一步

Day 15 主线：Evidence Verifier / Evidence Sufficiency。继续区分 Retrieval Relevance 与 Evidence Sufficiency，并保持生产 `AnswerService` 不因离线 Reranker 指标自动切换。

按 Agent Harness 补充说明，Day 15 仅让 Evidence Verifier 的输入输出能够映射到未来 Tool Schema；不实现 Tool Registry，不启动 Coding Agent。

### Day 20：lightweight AgentEvent trace foundation

- 新增轻量 `AgentEvent` contract，不实现 Agent Control Layer。
- 最小事件类型：`TOOL_CALL / TOOL_RESULT / EVIDENCE_HANDOFF`。
- `trace_id` 关联同一次 Repo Explorer 调查中的 ToolRuntime 与 Evidence handoff 事件。
- ToolRuntime 对每次调用记录调用摘要、结果摘要、延迟、错误码和 truncation 状态。
- RepoExplorer 在最终返回 EvidencePack 前记录 evidence handoff 摘要。
- Trace 只记录参数名和输出字段摘要，不复制完整 tool input、文件正文或 Evidence payload。
- Event sink 采用 best-effort；trace backend 失败不得改变正常 ToolResult / EvidencePack 结果。
- Trace 与业务状态分离；EvidencePack 不增加 trace_id。
- 未引入 LangGraph、LLM decision loop、shell、edit_file、git write 或 worktree modification。
- Full pytest：PASS。
- `git diff --check`：PASS。
- 既有 Starlette TestClient/httpx deprecation warning：非阻塞。

## Day 21–24：Code RAG 检索与结构能力

- Day 21：完成 Code Chunk、Keyword Code Retrieval、Dense Code Retrieval。
- Day 22：完成 RRF Code Hybrid Retrieval 与 `truncated` 完整性传播。
- Day 23：不单独重复实现；文件级代码证据已由 `read_file -> CodeEvidence -> EvidencePack` 覆盖。
- Day 24：完成 Python module、内部 import dependency、top-level symbol 提取并接入 Repo Explorer。
- 最新验证：
  - Full pytest：PASS
  - `git diff --check`：PASS
- 当前 working tree 累计包含 Day 19–24 的未提交改动。
- 下一真实能力缺口：静态调用关系 / 调用链线索。

## Day 27 checkpoint

- P3 Code RAG structural retrieval scalability refactor：PASS
- Structural snapshot/index：PASS
- Query-aware module retrieval：PASS
- Shared static-call snapshot：PASS
- `.local/` runtime corpus exclusion：PASS
- Code RAG Golden：12 cases
- Raw file hit：100%
- Explorer file hit：100%
- Evidence content hit：100%
- Provenance integrity：100%
- Full pytest：PASS
- `git diff --check`：PASS
- 当前工作区仍包含 Day25–27 未提交改动；commit/push 未执行。
- 下一步：Day28 P3 Gate，基于评测证据判断 P3 是否可以关闭以及哪些限制应进入后续 backlog。

## Day 28 P3 Gate

- Formal result: **CONDITIONAL PASS**
- Repo Explorer: **KEEP**
- P3 core implementation / safety / provenance / regression: PASS
- Day27 12-case Golden:
  - raw file hit 100%
  - Explorer file hit 100%
  - Evidence content hit 100%
  - provenance integrity 100%
- Remaining Gate condition: main handbook planned 50 Code RAG evaluation cases; current reviewed Golden has 12.
- This is an evaluation-coverage gap, not a production-code blocker.
- Day29: expand reviewed Code RAG Golden 12 → 50 and rerun the same evaluator.
- No new P3 feature work unless the expanded evaluation exposes a bounded high-priority defect.
- Do not close P3 milestone until final PASS and explicit user approval.

<!-- DAY29_P3_FINAL_GATE -->
## P3 Final Gate — Day29 (2026-08-16)

Status: **FINAL PASS WITH KNOWN LIMITATIONS**

Evidence:
- TechPilot 50 new held-out: 48/50 file hit, 46/50 strict Evidence-content hit,
  50/50 provenance integrity.
- Buku first-run external challenge: 12/15 file, 10/15 content, 15/15 provenance.
- yewtube fresh no-tuning challenge: 9/10 file, 8/10 content, 10/10 provenance.
- External first-run aggregate: 21/25 file, 18/25 content, 25/25 provenance.
- Oversized-class chunk guard experiment worsened Buku and was reverted.
- Repo Explorer decision: KEEP.
- No remaining safety/provenance/architecture blocker.

Known limitations:
- semantic localization != complete program semantics;
- repository organization affects retrieval quality;
- Hybrid is not universally better than Dense;
- exact Evidence granularity remains imperfect;
- static call clues are not a runtime call graph;
- indexes are in-memory full rebuild v1.

Next: Day30 P4 pre-learning / Thin Agent, Thick Harness.

<!-- DAY31_33_P4_STATUS_START -->
## P4 Day31–33 Checkpoint

P4 Day31–33 已完成第一轮 Research Agent implementation / control / routing / execution-policy 验证。

### 当前状态

- Day31：bounded Research Agent control baseline + real Repo workload = PASS。
- Day32：Unified LLM Reasoner + real gap-driven loop + Task Router = PASS。
- Day33：Execution Strategy tiering + query-focused Evidence context experiment = PASS。
- Day31 control matrix：7/7 PASS。
- Day32 routing baseline：12/12 PASS。
- 当前下一步：Day34 mixed real-business workload evaluation。

### 当前分级执行策略

```text
Workflow
→ deterministic / no LLM

Light Agent
→ DeepSeek Flash
→ max_steps=2
→ one explicit symbol: deterministic symbol-first
→ query-focused Evidence window

Research Agent
→ DeepSeek Pro
→ max_steps=5
→ dynamic action selection
→ prefix context baseline pending multi-source evaluation
```

### Day33 关键发现

“找到正确 source”不等于“LLM 当前看到的 Evidence 已覆盖问题”。

因此 P4 evaluation 从单一 `evidence_coverage` 进一步区分：

- `source_coverage`
- `decision_context_coverage`
- `grounded_completion`

受控实验中，同一个 Flash、同一个 2200-char Evidence budget、同一个 symbol-first，只把 Evidence selection 从 prefix 改为 query-focused：

- prefix：`max_steps`，task failure；
- query-focused：`completed`，1 step；
- latency 约 `3237 ms → 1644 ms`。

该结论目前只冻结到 Light Agent；Research Agent 的多-source context strategy 仍待 Day34+ 验证。

### 当前 P4 已知限制

- Router 仍是 heuristic baseline，真实泛化未充分证明。
- External Source + Repo 联合 research 尚未接入。
- Research Agent context strategy 尚未在 multi-source / conflict / long Evidence workload 上验证。
- structured decision repair exhausted 后仍需更完整的 structured failure path。
- Repository boundary violation 当前仍映射为较宽的 `execution_error`。

未经明确批准，不 commit / push / merge / tag / close milestone。
<!-- DAY31_33_P4_STATUS_END -->

<!-- P4-DAY30-37-STATUS-START -->
## P4 Day30–37：Research Agent — PASS WITH KNOWN LIMITATIONS

P4 最终结论：

**PASS WITH KNOWN LIMITATIONS**

Day30–37 已完成/验证：

- Thin Agent / Thick Harness P4 boundary；
- LangGraph bounded control；
- Unified Semantic Reasoner；
- Task Router + Workflow / Light / Research Execution Strategy；
- max_steps / retry / permanent failure / no actionable / completed termination；
- provider failure classification + control-layer bounded retry；
- RepoExplorer composite action failure propagation；
- `ActionExecutionOutcome`，分离 primitive ToolResult 与 composite action result；
- exact known-path authoritative materialization；
- UTF-8 sample-boundary safe read fix；
- cross-source documentation-drift verification；
- realistic-noise / contamination-safe Agent evaluation；
- full production RepoExplorer capability parity audit；
- semantic requirement-level success checks；
- source-role Evidence contract：tests 不能替代 production implementation evidence；
- bounded repair deterministic validation feedback；
- `DecisionReportFinalizer` user-facing conclusion + authoritative Sources。

Day36 final canonical v2：

```text
cases = 6
success = 4/6 = 66.7%
avg_source_coverage = 91.7%
semantic_false_positive_cases = 0
false_completion = 0
benchmark_exceptions = 0
benchmark_leakage_cases = 0
LLM calls = 21
tokens = 82624
agent latency ≈ 93583 ms
estimated cost ≈ $0.030805
```

Day37 real-business acceptance：

- complex 6-obligation release-readiness review：FAIL，暴露 multi-obligation decomposition / budget allocation 上限；
- provider-timeout incident：
  - 找到并修复 duplicate bounded-repair failure；
  - user-facing final synthesis 已打通；
  - 发现 test evidence 替代 production implementation 的 false completion；
  - source-role contract 将该 unsafe completion 转为 safe `NO_ACTIONABLE_PATH`。

P4 保留的真实 known limitations：

- semantic source/query planning 在同领域相似模块下不稳定；
- multi-obligation decomposition / budget allocation 不成熟；
- obligation expansion / goal drift；
- Research Agent 尚未接入 production FastAPI API composition；
- External Source + Repository joint research 尚未实现；
- service-level SSE / request lifetime / concurrency / P95/P99 留到 P7。

下一阶段：

> **Day38 进入 P5：JD Structured Output / 岗位与项目证据。**

P5 复用 P3/P4 Tool / Evidence / Trace / structured failure / evaluation Harness，不另造 Agent runtime。
<!-- P4-DAY30-37-STATUS-END -->

<!-- DAY37_5_PRODUCT_UI_START -->
## Day37.5 — Product UI Foundation

**Status: PASS**

在 P4 closeout 与 P5 Day38 之间增加产品界面收尾，不扩展 Agent 能力。

已完成：

- FastAPI-served Product UI：`app/product_ui/`；
- evidence-grounded workspace Q&A；
- contextual Evidence panel；
- PDF / Markdown ingestion UI；
- session-local source library / ingestion detail；
- system dependency health；
- real Workspace list / create / select / delete；
- non-empty Workspace deletion `409` fail-closed；
- light gray-blue translucent visual system + desktop typography pass；
- frontend / workspace API regression tests。

明确未声称：

- persistent document listing API；
- P5 JD extraction backend；
- Research Agent production FastAPI composition。

下一步：**Day38 / P5 — JD Structured Output**。
<!-- DAY37_5_PRODUCT_UI_END -->

<!-- EVAL_BACKFILL_20260822_START -->
## Evaluation Backfill — COMPLETE（2026-08-22）

### 状态

- Document Retrieval：**CLOSED**
- Answer / Evidence：**CLOSED**
- OCR：**CLOSED WITH KNOWN LIMITATION**
- Code RAG：**CLOSED WITH KNOWN LIMITATIONS**
- Research Agent：**CLOSED WITH KNOWN LIMITATIONS**
- Evaluation Backfill：**COMPLETE**
- 下一阶段：恢复 **P5 Day38–42：JD Structured Output / 岗位与项目证据**

### 评测资产边界

本轮回填的目标是补足“简历数字、失败归因、优化证据”，不是把所有数据包装成 clean heldout。

- Document 400：assistant-reviewed frozen candidate set；`review_status=machine_validated`；不是 clean heldout。
- Answer 180：machine/assistant lineage；不是 human-reviewed / clean heldout。
- Code RAG 150：mixed legacy + deterministic-validated structural/regression candidate set；不是 clean heldout。
- Code RAG hard-30：task-oriented realistic check；assistant-curated + local authoritative source validation；不是 clean heldout。
- Research Agent 36：machine-validated backfill；positive source/marker truth 在当前本地仓库验证；不是 clean heldout。

### Document Retrieval

冻结 corpus：

- 30 documents
- 2345 canonical units
- corpus_version：`batch2-final-v1`

400-case frozen evaluation：

```text
Dense
Recall@5          61.5%
Evidence Hit@5    64.5%
MRR@5             44.4%
nDCG@5            47.8%
Coverage           63.0%

Hybrid + CrossEncoder
Recall@5          83.4%
Evidence Hit@5    86.0%
MRR@5             72.6%
nDCG@5            74.9%
Coverage           84.7%
P95                867.4 ms
```

最终候选方案：

```text
1200-char structure-aware / no overlap
+ multilingual-e5-base Dense
+ BM25
+ RRF
+ CrossEncoder reranker
```

相对 Dense：

- Recall：`+21.9pp`
- Evidence Hit：`+21.5pp`
- Coverage：`+21.7pp`
- MRR：`+28.2pp`
- nDCG：`+27.1pp`

同时修复大批量 Qdrant request 超限问题：embedding / upsert 改为 bounded batching，并保留 document compensation delete。

### Answer / Evidence

180-case answer/evidence evaluation：

- runtime errors：2
- answered：146
- over-refusal：32
- citation hit：86.30%
- document citation hit：96.58%
- strict citation precision：68.29%
- strict citation recall：84.25%
- evidence coverage：85.62%

Assistant audit（146 answered）：

- full correct：140 / 146 = **95.89%**
- partial：4
- incorrect：2
- full-correct E2E yield：77.78%
- partial+ E2E yield：80.00%

Verifier Policy v2 在 20-case source-binding adversarial set：

- correct refusal：19/20
- false-answer：**5.0%**

Provider Retry v2：

- bounded retry
- transient-only retry
- structured-output single repair
- non-retryable 4xx fail-fast
- focused tests：100% PASS

### OCR

- native/scanned paired benchmark：20 real pages × 2 queries = 40 query cases
- scanned ingestion：100%
- 原 projection success：75%
- targeted PSM6 → PSM3：
  - projection：16.7% → 75.0%
  - `+58.3pp`
  - rescued 7/10 previous failures

已知限制：

> 复杂 table / checklist reading order 仍需要 layout-aware parsing；不继续做无界 OCR engine / DPI sweep。

### Code RAG

150-case structural/regression benchmark：

```text
File Hit@5             94.67%
Evidence Content Hit   89.33%
Strict Exact Symbol    87.68%
MRR                    86.78%
File nDCG@5            88.80%
Provenance Integrity  100.00%
```

30-case task-oriented hard benchmark：

```text
File Hit@5             93.33%
Evidence Content Hit   93.33%
Strict Exact Symbol    80.00%
MRR                    74.28%
Provenance Integrity  100.00%
```

Hard-30 audit：

- 4 个 strict-symbol miss 实际命中 enclosing class；
- 1 个 file miss 属于 overly narrow Golden；
- 1 个属于 document/code hybrid identity guard 的 query ambiguity；
- 未观察到明显完全无关的 retrieval failure。

Targeted identifier-rich routing probe：

```text
Keyword -> Hybrid

File Hit       80%   -> 100%
Content Hit    64%   -> 100%
Exact Symbol   64%   -> 100%
MRR            56.3% -> 88.0%
```

该 probe 仅证明 Hybrid 更适合该 identifier-rich derived subset；不作为真实业务整体提升数字。

### Research Agent

36-case machine-validated backfill，在 **full Code RAG capability surface** 下：

```text
Overall case pass                    55.56%
Positive grounded success            46.67%
Positive source coverage             78.33%
Decision-context coverage            65.00%

Negative outcome correctness        100.00%
False completion on negatives         0
Provenance integrity                100.00%
```

分类：

- source-role production authority：6/6
- unsupported production claim：6/6
- failure recovery：3/6
- known-source refinement：3/6
- multi-obligation release review：2/6
- obligation persistence / goal drift：0/6

关键诊断：

1. 第一轮旧 Day34 evaluator 只暴露 `symbol/code/read`，造成大量 zero-evidence loop；该 `16.7%` 仅作为 **legacy evaluator wiring mismatch diagnostic**，不是最终 Research Agent baseline。
2. 接入当前完整 Code RAG surface 后，case pass `16.7% -> 55.6%`，source coverage `41.7% -> 78.3%`，≥2 zero-evidence cases `27 -> 5`。
3. Hybrid 共使用 38 次且无 zero-evidence，说明当前主要瓶颈不是 Code RAG。
4. `PREFIX -> QUERY_FOCUSED` 的 12-case targeted probe 反而降低 success / source coverage，因此拒绝合入。
5. 当前真实 limitation 集中在 semantic planning、multi-obligation decomposition、unresolved obligation persistence / goal drift、known-source refinement。

### Gate 决策

Evaluation Backfill 到此停止，不再做：

- broad prompt sweep；
- retrieval 参数网格；
- benchmark 扩容；
- 为少数 case 写特例 heuristic。

后续只有在 P5/P6 真实业务链路暴露具体 regression 时，才重新打开对应 capability。

<!-- EVAL_BACKFILL_20260822_END -->

<!-- P5_DAY38_20260824_START -->
## P5 Day38 — JD Structured Output / Job Workflow Foundation

### 状态

```text
Engineering contract       PASS
Full regression            PASS
Real JD validation         OPEN
Real Job Discovery         OPEN
Day38 overall              CONDITIONAL PASS
P5 Gate                    OPEN
```

### 已完成

- 冻结 provider-neutral JD structured extraction contract。
- `StructuredJD` / `JDRequirement` 使用 Pydantic validation。
- requirement 保留：
  - normalized skill
  - category
  - required/preferred
  - importance
  - original `EvidenceSpan(text/start/end)`
- DeepSeek adapter 输出经过 schema gate；malformed output 只做 bounded repair。
- evidence span 必须与原 JD 绑定；不允许通过伪造 `start=0` 等方式“修好”无效模型输出。
- 建立 deterministic skill normalization 第一版。
- 建立 Job domain：
  - `UserJobIntent / JobSearchSpec`
  - `JobRecord`
  - `JobDiscoveryProvider`
  - normalization / quality / deduplication pipeline
  - optional capability profile matching
  - ranking / recommendation service
- Mock discovery 仅作为 contract test provider；production 不默认接 Mock。
- 最终 P5 Job/JD 层不依赖 Code RAG / `EvidencePack` / `CodeEvidence` / Research Agent runtime。

### 本轮 architecture correction

曾错误引入：

- `app/job/agent/`
- `app/job/tools/`
- Job -> Harness / EvidencePack adapter
- 重复 intelligence / recommendation 模块
- production Mock provider wiring

全部从最终净设计移除。

原因：

> JD extraction 是固定输入 → structured extraction → validation / bounded repair 的 constrained workflow；Job Discovery 是 provider boundary。当前没有业务理由为 P5 再造第二套 Agent/Harness。

### Regression repairs

- PDF/OCR direct-parser compatibility 与 production OCR threshold 分离。
- loopback Qdrant HTTP/SDK 请求绕过 environment proxy，消除 localhost 502。
- PostgreSQL dependency health probe 使用独立 `NullPool` engine，避免 pooled asyncpg connection 跨 event loop 复用。
- Full regression：100% PASS。
- `git diff --check`：PASS。

### 未完成 / 不允许夸大

- 尚未接 production real Job Discovery provider。
- 尚未建立总控手册要求的真实 JD seed / 30–50 real JD evaluation。
- 当前 synthetic / fake-provider tests 只证明 contract，不是 real-world product validation。
- 尚不能宣布 P5 Gate PASS。
- Job matching 与 Code RAG 无关；不得恢复该耦合。

### 下一步

```text
real user query
→ real job source
→ real JD
→ StructuredJD
→ extraction evaluation
→ failure attribution
→ bounded optimization
```
<!-- P5_DAY38_20260824_END -->

<!-- P5_PRODUCT_BOUNDARY_20260824 -->
### P5 Product Boundary Correction

P5 正式产品需求冻结为：

1. Job Intent → real job recommendation；
2. Resume → real job recommendation；
3. Resume + JD → fit analysis / match score / gaps。

Code RAG 与 P5 完全独立，不参与上述任一链路，也不规划 `JD requirement → repository evidence`。

<!-- TECHPILOT_JOB_INTELLIGENCE_CLOSEOUT_START -->
## 2026-08-24 — Job Intelligence Prototype Closeout (supersedes active P5 roadmap)

**Status: CLOSED AS A BUSINESS PROTOTYPE — NOT A PRODUCT GATE PASS.**

The earlier active roadmap language describing P5 as `JD requirement -> repository evidence` is superseded. Job Intelligence and Code RAG are separate capabilities.

Frozen Job Intelligence flows:

```text
A. intent -> real jobs -> structured JD -> rank/recommend
B. resume -> profile -> real jobs -> match/rank/recommend
C. resume + JD -> fit / satisfied / gaps / explanation
```

Real validation evidence:

| Source/flow | Result |
| --- | --- |
| Nowcoder acquisition | 10/10 live jobs |
| Nowcoder structural | 5/5 grounded JDs, 0 model repair |
| Nowcoder Flow A | 5 jobs, 0 analysis failures |
| Shixiseng acquisition | 8/10 live jobs |
| Shixiseng structural | 5 grounded successes across 6 evaluated; 1 retained failure |
| Shixiseng Flow A | 4 jobs, 0 analysis failures |
| BOSS HTTP | blocked by security challenge; no bypass |
| BOSS browser v8 | 18 listings, 0 reliable full JDs |
| Resume E2E | PDF read succeeded; profile evidence binding failed |
| Flow B/C | NOT CLOSED |

Product conclusion:

The dominant unresolved problem became Chinese recruitment-source coverage, especially stable BOSS full-JD discovery. Continuing would shift the project toward browser/site acquisition engineering rather than the intended applied-AI focus.

Next candidate direction: **AI Coding**, but implementation is blocked on a product-differentiation thesis versus Codex / Claude Code / Cursor.

See `docs/P5_JOB_INTELLIGENCE_CLOSEOUT.md`.
<!-- TECHPILOT_JOB_INTELLIGENCE_CLOSEOUT_END -->
